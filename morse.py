import sys
import time
import array
import math
import pygame
import moderngl
import ctypes

# ==============================================================================
# 1. 基础配置与常量定义
# ==============================================================================
VIRTUAL_WIDTH, VIRTUAL_HEIGHT = 1000, 600
ASPECT_RATIO = VIRTUAL_WIDTH / VIRTUAL_HEIGHT

INITIAL_WPM = 15
INITIAL_DOT_DURATION = 1.2 / INITIAL_WPM
WINDOW_SIZE = 15

# 摩尔斯电码字典
MORSE_DICT = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F', '--.': 'G',
    '....': 'H', '..': 'I', '.---': 'J', '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N',
    '---': 'O', '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T', '..-': 'U',
    '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y', '--..': 'Z', '.----': '1',
    '..---': '2', '...--': '3', '....-': '4', '.....': '5', '-....': '6', '--...': '7',
    '---..': '8', '----.': '9', '-----': '0'
}

# ==============================================================================
# 2. GLSL 着色器源码 (OpenGL)
# ==============================================================================
VERTEX_SHADER = """
#version 330
in vec2 in_vert;
in vec2 in_texcoord;
out vec2 v_texcoord;

void main() {
    gl_Position = vec4(in_vert, 0.0, 1.0);
    v_texcoord = vec2(in_texcoord.x, 1.0 - in_texcoord.y);
}
"""

PERSISTENCE_SHADER = """
#version 330
uniform sampler2D u_current_frame;
uniform sampler2D u_prev_frame;
uniform float u_decay; 
in vec2 v_texcoord;
out vec4 f_color;

void main() {
    vec4 current = texture(u_current_frame, v_texcoord);
    vec4 prev = texture(u_prev_frame, v_texcoord);
    f_color = max(current, prev * u_decay);
}
"""

FRAGMENT_SHADER = """
#version 330
uniform sampler2D u_texture;
uniform float u_time;
uniform bool u_crt_on; 
in vec2 v_texcoord;
out vec4 f_color;

vec2 distort(vec2 uv) {
    uv = (uv - 0.5) * 2.0;
    uv.x *= 1.0 + pow((uv.y / 9.0), 2.0); 
    uv.y *= 1.0 + pow((uv.x / 8.0), 2.0);
    return (uv / 2.0) + 0.5;
}

float pseudo_random(vec2 co) {
    return fract(sin(dot(co.xy, vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
    if (!u_crt_on) {
        f_color = texture(u_texture, v_texcoord);
        return;
    }

    vec2 uv = distort(v_texcoord);
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // 辉光 (Glow Effect)
    vec3 glow = vec3(0.0);
    float glow_weights[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 base_color = texture(u_texture, uv).rgb;
    glow += base_color * glow_weights[0];
    for(int i = 1; i < 5; i++) {
        float offset = float(i) * 0.0012; 
        glow += texture(u_texture, uv + vec2(offset, 0.0)).rgb * glow_weights[i];
        glow += texture(u_texture, uv - vec2(offset, 0.0)).rgb * glow_weights[i];
    }

    // 色差色散 (Chromatic Aberration)
    vec3 crt_color;
    crt_color.r = texture(u_texture, uv + vec2(0.001, 0.0)).r;
    crt_color.g = base_color.g;
    crt_color.b = texture(u_texture, uv - vec2(0.001, 0.0)).b;
    crt_color += glow * 1.1; 

    // 噪点模拟 (Grain)
    float grain = pseudo_random(uv + sin(u_time)) * 0.06;
    crt_color += vec3(grain);

    // 屏幕闪烁 (Flicker)
    float flicker = 0.985 + 0.015 * sin(u_time * 50.0) * cos(u_time * 20.0);
    crt_color *= flicker;

    // 滚动条纹 (Rolling Scanbar)
    float wave = sin(uv.y * 3.14159 - u_time * 0.6);
    float bar_factor = pow(wave * 0.5 + 0.5, 128.0);
    crt_color *= mix(1.0, 0.55, bar_factor);

    // 扫描线 (Scanlines)
    float scanline = sin(uv.y * 550.0 * 3.14159) * 0.12; 
    crt_color -= vec3(scanline);
    crt_color *= (0.78 + 0.22 * sin(uv.x * 750.0 * 3.14159));

    // 暗角 (Vignette)
    float vignette = uv.x * uv.y * (1.0 - uv.x) * (1.0 - uv.y);
    f_color = vec4(crt_color * clamp(pow(16.0 * vignette, 0.15), 0.0, 1.0), 1.0);
}
"""


# ==============================================================================
# 3. 系统级辅助函数 (DPI, IME, 视口计算)
# ==============================================================================
def init_windows_dpi():
    """在 Windows 系统下启用高 DPI 适配，防止鼠标点击错位"""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def disable_ime():
    """禁用系统输入法，避免干扰按键响应"""
    if sys.platform == "win32":
        hwnd = ctypes.windll.user32.GetActiveWindow()
        if hwnd:
            ctypes.windll.imm32.ImmAssociateContext(hwnd, 0)


def calculate_viewport(window_w, window_h):
    """根据当前窗口大小计算保持宽横比的 OpenGL 视口"""
    window_ratio = window_w / window_h
    if window_ratio > ASPECT_RATIO:
        vp_w = int(window_h * ASPECT_RATIO)
        return (window_w - vp_w) // 2, 0, vp_w, window_h
    else:
        vp_h = int(window_w / ASPECT_RATIO)
        return 0, (window_h - vp_h) // 2, window_w, vp_h


def generate_beep_sound(frequency=1000, sample_rate=44100):
    """生成正弦波蜂鸣声"""
    audio_buffer = array.array('h', [0] * (sample_rate * 2))
    for i in range(sample_rate):
        t = float(i) / sample_rate
        value = int(32767 * math.sin(2 * math.pi * frequency * t))
        audio_buffer[i * 2] = audio_buffer[i * 2 + 1] = value
    return pygame.mixer.Sound(buffer=audio_buffer)


# ==============================================================================
# 4. UI 图标绘制函数
# ==============================================================================
def draw_mute_icon(screen, rect, is_muted):
    cx, cy = rect.center
    color = (65, 85, 90) if is_muted else (0, 255, 200)

    # 喇叭本体
    pygame.draw.rect(screen, color, (cx - 14, cy - 5, 6, 10))
    pygame.draw.polygon(
        screen,
        color,
        [(cx - 8, cy - 5), (cx - 2, cy - 10), (cx - 2, cy + 10), (cx - 8, cy + 5)],
    )

    # 右侧状态
    if is_muted:
        # 静音状态绘制红色 "X"
        pygame.draw.line(screen, (255, 90, 90), (cx + 4, cy - 5), (cx + 14, cy + 5), 2)
        pygame.draw.line(screen, (255, 90, 90), (cx + 4, cy + 5), (cx + 14, cy - 5), 2)
    else:
        # 正常状态：绘制声波弧线
        for r, w in [(16, 8), (28, 14)]:
            pygame.draw.arc(
                screen,
                color,
                (cx + 2 - w, cy - w, r, r),
                -math.pi / 3,
                math.pi / 3,
                2,
            )


def draw_crt_icon(screen, rect, crt_on):
    cx, cy = rect.center
    color = (0, 255, 200) if crt_on else (65, 85, 90)
    # 显示器外框
    pygame.draw.rect(screen, color, (cx - 14, cy - 10, 28, 20), 2, border_radius=3)
    # 内屏
    pygame.draw.rect(screen, color, (cx - 10, cy - 7, 18, 14), 1)
    # 天线
    pygame.draw.line(screen, color, (cx - 6, cy - 10), (cx - 12, cy - 16), 2)
    pygame.draw.line(screen, color, (cx + 6, cy - 10), (cx + 12, cy - 16), 2)


def draw_fullscreen_icon(screen, rect, is_fullscreen):
    cx, cy = rect.center
    color = (0, 255, 200) if is_fullscreen else (65, 85, 90)
    size = 11
    # 绘制四个角的直角扩展边框
    pygame.draw.lines(screen, color, False,
                      [(cx - size, cy - size + 5), (cx - size, cy - size), (cx - size + 5, cy - size)], 2)
    pygame.draw.lines(screen, color, False,
                      [(cx + size - 5, cy - size), (cx + size, cy - size), (cx + size, cy - size + 5)], 2)
    pygame.draw.lines(screen, color, False,
                      [(cx - size, cy + size - 5), (cx - size, cy + size), (cx - size + 5, cy + size)], 2)
    pygame.draw.lines(screen, color, False,
                      [(cx + size - 5, cy + size), (cx + size, cy + size), (cx + size, cy + size - 5)], 2)


# ==============================================================================
# 5. 主程序核心逻辑
# ==============================================================================
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def main():
    # 5.1 初始化 Pygame 与音频配置
    init_windows_dpi()
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=256, allowedchanges=0)
    pygame.init()
    if hasattr(pygame.key, 'stop_text_input'):
        pygame.key.stop_text_input()

    current_window_w, current_window_h = 1000, 600
    pygame.display.set_mode((current_window_w, current_window_h), pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
    pygame.display.set_caption("Morse Terminal")

    try:
        pygame.display.set_icon(pygame.image.load("morse.jpg"))
    except Exception:
        pass
    disable_ime()

    # 5.2 初始化 ModernGL 与着色器编译
    ctx = moderngl.create_context()
    pg_surface = pygame.Surface((VIRTUAL_WIDTH, VIRTUAL_HEIGHT))

    prog_crt = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
    prog_persist = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=PERSISTENCE_SHADER)

    # 顶点数据配置（2D 全屏贴图纹理坐标映射）
    vbo_data = array.array('f', [
        -1.0, 1.0, 0.0, 0.0,
        -1.0, -1.0, 0.0, 1.0,
        1.0, 1.0, 1.0, 0.0,
        1.0, -1.0, 1.0, 1.0
    ])
    vbo = ctx.buffer(vbo_data)
    vao_crt = ctx.vertex_array(prog_crt, [(vbo, '2f 2f', 'in_vert', 'in_texcoord')], mode=moderngl.TRIANGLE_STRIP)
    vao_persist = ctx.vertex_array(prog_persist, [(vbo, '2f 2f', 'in_vert', 'in_texcoord')],
                                   mode=moderngl.TRIANGLE_STRIP)

    # 纹理与帧缓冲双缓冲（用于生成 CRT 荧光屏绿光残影余晖）
    texture_input = ctx.texture((VIRTUAL_WIDTH, VIRTUAL_HEIGHT), 4)
    texture_input.filter = (moderngl.LINEAR, moderngl.LINEAR)

    persist_tex_A = ctx.texture((VIRTUAL_WIDTH, VIRTUAL_HEIGHT), 4)
    persist_tex_B = ctx.texture((VIRTUAL_WIDTH, VIRTUAL_HEIGHT), 4)
    fbo_A = ctx.framebuffer(color_attachments=[persist_tex_A])
    fbo_B = ctx.framebuffer(color_attachments=[persist_tex_B])

    for f in [fbo_A, fbo_B]:
        f.clear(0, 0, 0, 1)
    for t in [persist_tex_A, persist_tex_B]:
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)

    # 5.3 变量及状态初始化
    flip_flop = True
    try:
        font_large = pygame.font.SysFont("Courier New", 54, bold=True)
        font_small = pygame.font.SysFont("Courier New", 26, bold=True)
        font_menu = pygame.font.SysFont("Courier New", 18, bold=True)
    except Exception:
        font_large = pygame.font.SysFont("Arial", 54)
        font_small = pygame.font.SysFont("Arial", 26)
        font_menu = pygame.font.SysFont("Arial", 18)

    beep = generate_beep_sound()
    is_pressing, press_start_time, release_start_time = False, 0, time.time()
    current_code, decoded_text, current_wpm = "", "", INITIAL_WPM
    is_muted, crt_on, is_fullscreen = False, True, False

    # UI 交互热区
    mute_rect = pygame.Rect(VIRTUAL_WIDTH - 60, 15, 40, 40)
    crt_rect = pygame.Rect(VIRTUAL_WIDTH - 110, 15, 40, 40)
    fullscreen_rect = pygame.Rect(VIRTUAL_WIDTH - 160, 15, 40, 40)

    is_started, has_broken_char, has_broken_word, scroll_speed = False, True, True, 3
    dot_duration_history = [INITIAL_DOT_DURATION] * WINDOW_SIZE
    wave_history = [390.0] * VIRTUAL_WIDTH  # 变更为浮点数以支持平滑的时间步进
    clock = pygame.time.Clock()
    start_time_stamp = time.time()

    # 累积的时间余量，用于解决亚像素级别的方波步进
    accumulated_time = 0.0

    # 5.4 内部辅助逻辑
    def toggle_screen_mode():
        if sys.platform == "win32":
            pt = POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            pygame.display.toggle_fullscreen()
            ctypes.windll.user32.SetCursorPos(pt.x, pt.y)
        else:
            pygame.display.toggle_fullscreen()
        disable_ime()

    def sync_audio_state():
        if is_pressing:
            beep.stop() if is_muted else beep.play(-1)

    # 5.5 主循环开始
    while True:
        # 获取两帧之间的时间差（秒数），用于独立于帧率的平滑渲染
        # 如果你想限制在 120 帧，将 tick() 参数改为 120
        # 如果想完全不限制帧率（不锁帧），将 tick() 参数改为 0
        dt = clock.tick(0) / 1000.0
        current_time = time.time()
        avg_dot_duration = sum(dot_duration_history) / WINDOW_SIZE

        # 判定阈值计算
        dash_threshold = avg_dot_duration * 2.0
        char_timeout = avg_dot_duration * 3.0
        word_timeout = avg_dot_duration * 7.0

        # --- 事件处理阶段 ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()

            elif event.type == pygame.VIDEORESIZE:
                current_window_w, current_window_h = event.w, event.h

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # 鼠标坐标还原成虚拟画布像素点
                vp_x, vp_y, vp_w, vp_h = calculate_viewport(current_window_w, current_window_h)
                v_mx = int((event.pos[0] - vp_x) * (VIRTUAL_WIDTH / vp_w))
                v_my = int((event.pos[1] - vp_y) * (VIRTUAL_HEIGHT / vp_h))

                if mute_rect.collidepoint((v_mx, v_my)):
                    is_muted = not is_muted
                    sync_audio_state()
                elif crt_rect.collidepoint((v_mx, v_my)):
                    crt_on = not crt_on
                elif fullscreen_rect.collidepoint((v_mx, v_my)):
                    is_fullscreen = not is_fullscreen
                    toggle_screen_mode()

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE and not is_pressing:
                    is_pressing, is_started, press_start_time = True, True, current_time
                    if not is_muted:
                        beep.play(-1)
                elif event.key == pygame.K_RIGHT:
                    scroll_speed = min(15, scroll_speed + 1)
                elif event.key == pygame.K_LEFT:
                    scroll_speed = max(1, scroll_speed - 1)
                elif event.key == pygame.K_m:
                    is_muted = not is_muted
                    sync_audio_state()
                elif event.key == pygame.K_c:
                    crt_on = not crt_on
                elif event.key in (pygame.K_F11, pygame.K_f):
                    is_fullscreen = not is_fullscreen
                    toggle_screen_mode()
                elif event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    sys.exit()

            elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE and is_pressing:
                is_pressing = False
                beep.stop()
                release_start_time = current_time
                has_broken_char = has_broken_word = False

                # 记录录入信号并调整自动识别速度 (WPM 动态学习)
                duration = current_time - press_start_time
                current_code += "-" if duration >= dash_threshold else "."

                measured_dot = max(1.2 / 60, min(1.2 / 5, duration if duration < dash_threshold else duration / 3.0))
                dot_duration_history.pop(0)
                dot_duration_history.append(measured_dot)
                current_wpm = 1.2 / (sum(dot_duration_history) / WINDOW_SIZE)

                # 六点清屏判定
                if current_code == "......":
                    current_code = decoded_text = ""
                    has_broken_char = has_broken_word = True

        # --- 自动断字/断词判定时间线 ---
        if not is_pressing:
            silence = current_time - release_start_time
            if not has_broken_char and silence >= char_timeout:
                if current_code:
                    decoded_text += MORSE_DICT.get(current_code, "?")
                    current_code = ""
                has_broken_char = True

            if not has_broken_word and silence >= word_timeout:
                if decoded_text and not decoded_text.endswith(" "):
                    decoded_text += " "
                has_broken_word = True

        # --- 独立于帧率的方波历史位移计算 ---
        # 60fps基准下每秒移动 60 * scroll_speed 个像素。
        # 这里通过 dt 计算出当前帧应该移动的精确像素点数，防止高 FPS 下波形跑得太快。
        accumulated_time += dt
        pixels_to_move = int(accumulated_time * 60.0 * scroll_speed)
        if pixels_to_move > 0:
            accumulated_time -= pixels_to_move / (60.0 * scroll_speed)
            # 限制单帧最大位移，防止极端掉帧引起的波形瞬移
            pixels_to_move = min(pixels_to_move, VIRTUAL_WIDTH)

            wave_history = wave_history[pixels_to_move:] + [290.0 if is_pressing else 390.0] * pixels_to_move

        # --- 2D 画布渲染阶段 ---
        pg_surface.fill((8, 14, 10))

        # 绘制主解码文本
        display_text = decoded_text[-26:] if decoded_text else ("Press Space to Start" if not is_started else "")
        text_surface = font_large.render(display_text, True, (0, 245, 180))
        pg_surface.blit(text_surface, text_surface.get_rect(center=(VIRTUAL_WIDTH // 2, 145)))

        # 绘制当前正在录入的代码 (. / -)
        code_surface = font_small.render(current_code, True, (0, 255, 200))
        pg_surface.blit(code_surface, code_surface.get_rect(center=(VIRTUAL_WIDTH // 2, 210)))

        # 生成方波线段点阵
        square_wave_points = []
        for x in range(VIRTUAL_WIDTH):
            if x > 0 and wave_history[x] != wave_history[x - 1]:
                square_wave_points.append((x, int(wave_history[x - 1])))
            square_wave_points.append((x, int(wave_history[x])))
        if len(square_wave_points) > 1:
            pygame.draw.lines(pg_surface, (0, 230, 140), False, square_wave_points, 3)

        # 渲染底部状态和菜单
        pg_surface.blit(font_small.render(f"WPM: {current_wpm:.1f}", True, (230, 170, 40)),
                        (VIRTUAL_WIDTH - 165, VIRTUAL_HEIGHT - 45))

        # # 实时显示 FPS，方便你观察不锁帧时的性能
        # fps_text = f"FPS: {clock.get_fps():.0f}"
        # pg_surface.blit(font_small.render(fps_text, True, (100, 110, 105)), (VIRTUAL_WIDTH - 300, VIRTUAL_HEIGHT - 45))

        # 构造进度条
        max_ticks = 15
        bar_filled = "■" * scroll_speed
        bar_empty = "□" * (max_ticks - scroll_speed)
        speed_bar_text = f"WAVE SPEED  [{bar_filled}{bar_empty}]"

        speed_surf = font_small.render(speed_bar_text, True, (0, 255, 200))
        pg_surface.blit(speed_surf, (25, VIRTUAL_HEIGHT - 45))

        menu_items = ["[Left / Right] : Adjust Speed",
                      "[......] (6 dots) : Clear All",
                      "[Esc] : Quit",]
        for index, text in enumerate(menu_items):
            pg_surface.blit(font_menu.render(text, True, (55, 95, 85)), (20, 18 + index * 24))

        # 绘制交互图标
        draw_mute_icon(pg_surface, mute_rect, is_muted)
        draw_crt_icon(pg_surface, crt_rect, crt_on)
        draw_fullscreen_icon(pg_surface, fullscreen_rect, is_fullscreen)

        # 绘制图标下的单键快捷键文本指示
        f_color = (0, 255, 200) if is_fullscreen else (65, 85, 90)
        c_color = (0, 255, 200) if crt_on else (65, 85, 90)
        m_color = (65, 85, 90) if is_muted else (0, 255, 200)

        f_surf = font_menu.render("F", True, f_color)
        c_surf = font_menu.render("C", True, c_color)
        m_surf = font_menu.render("M", True, m_color)

        pg_surface.blit(f_surf, f_surf.get_rect(center=(fullscreen_rect.centerx, fullscreen_rect.bottom + 12)))
        pg_surface.blit(c_surf, c_surf.get_rect(center=(crt_rect.centerx, crt_rect.bottom + 12)))
        pg_surface.blit(m_surf, m_surf.get_rect(center=(mute_rect.centerx, mute_rect.bottom + 12)))

        # --- GPU着色器混合与后期处理阶段 ---
        texture_input.write(pygame.image.tostring(pg_surface, 'RGBA', True))
        vp_x, vp_y, vp_w, vp_h = calculate_viewport(current_window_w, current_window_h)

        # 切换前后帧缓冲区（残影计算）
        active_fbo = fbo_A if flip_flop else fbo_B
        prev_tex = persist_tex_B if flip_flop else persist_tex_A
        out_tex = persist_tex_A if flip_flop else persist_tex_B

        # 动态计算余晖衰减：根据当前的 dt 来调整每次混合时的衰减度
        # 原本 60 帧（0.0166秒）下是 0.68，那么在高帧率或更低帧率下自动进行指数映射
        if crt_on:
            current_decay = math.pow(0.60, dt / (1.0 / 60.0))
        else:
            current_decay = 0.0

        # 渲染余晖残影着色器
        active_fbo.use()
        ctx.viewport = (0, 0, VIRTUAL_WIDTH, VIRTUAL_HEIGHT)
        texture_input.use(0)
        prev_tex.use(1)
        prog_persist['u_current_frame'].value = 0
        prog_persist['u_prev_frame'].value = 1
        prog_persist['u_decay'].value = current_decay
        vao_persist.render()

        # 渲染最终的 CRT 模拟着色器到屏幕
        ctx.screen.use()
        ctx.clear(0.0, 0.0, 0.0, 1.0)
        ctx.viewport = (vp_x, vp_y, vp_w, vp_h)
        (out_tex if crt_on else texture_input).use(0)

        flip_flop = not flip_flop

        if 'u_time' in prog_crt:
            prog_crt['u_time'].value = time.time() - start_time_stamp
        if 'u_crt_on' in prog_crt:
            prog_crt['u_crt_on'].value = crt_on
        vao_crt.render()

        pygame.display.flip()


if __name__ == "__main__":
    main()
