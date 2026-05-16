import os
import sys
import time
import array
import math
import random
import pygame
import moderngl  # 导入现代 OpenGL 绑定库

# --- 核心参数配置保持不变 ---
INITIAL_WPM = 15
INITIAL_DOT_DURATION = 1.2 / INITIAL_WPM
WINDOW_SIZE = 15

MORSE_DICT = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E',
    '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I', '.---': 'J',
    '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O',
    '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T',
    '..': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y',
    '--..': 'Z', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
    '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9',
    '-----': '0'
}

# =====================================================================
# 【GLSL 着色器代码】1. 新增残影混合着色器 2. 优化主渲染着色器
# =====================================================================
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

# 用于将“前一帧的残影”与“当前输入帧”进行物理混合的着色器
PERSISTENCE_SHADER = """
#version 330
uniform sampler2D u_current_frame;
uniform sampler2D u_prev_frame;
uniform float u_decay; // 荧光粉衰减系数
in vec2 v_texcoord;
out vec4 f_color;
void main() {
    vec4 current = texture(u_current_frame, v_texcoord);
    vec4 prev = texture(u_prev_frame, v_texcoord);
    // 残影衰减混合：当前帧亮部会瞬间照亮，暗下去时会保留旧帧的遗留
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

// 1. 稍微加重一点桶形畸变
vec2 distort(vec2 uv) {
    uv = (uv - 0.5) * 2.0;
    uv.x *= 1.0 + pow((uv.y / 9.0), 2.0); 
    uv.y *= 1.0 + pow((uv.x / 8.0), 2.0);
    uv = (uv / 2.0) + 0.5;
    return uv;
}

// 2. 高效的 GPU 伪随机噪点
float pseudo_random(vec2 co) {
    return fract(sin(dot(co.xy ,vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
    // 如果关闭了 CRT 效果，直接输出原始高清纹理
    if (!u_crt_on) {
        f_color = texture(u_texture, v_texcoord);
        return;
    }

    // --- CRT 效果开启时的渲染逻辑 ---
    vec2 uv = distort(v_texcoord);
    
    // 玻璃边界裁剪
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        f_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }

    // 3. 【横向荧光粉光晕扩散（物理模拟眩光）】
    vec3 glow = vec3(0.0);
    float glow_weights[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    
    vec3 base_color = texture(u_texture, uv).rgb;
    glow += base_color * glow_weights[0];
    
    for(int i = 1; i < 5; i++) {
        float offset = float(i) * 0.0012; // 眩光宽度控制
        glow += texture(u_texture, uv + vec2(offset, 0.0)).rgb * glow_weights[i];
        glow += texture(u_texture, uv - vec2(offset, 0.0)).rgb * glow_weights[i];
    }
    
    // 4. 【微观网格物理错位】模拟电子枪三原色发发光溢出
    vec3 crt_color;
    crt_color.r = texture(u_texture, uv + vec2(0.001, 0.0)).r;
    crt_color.g = base_color.g;
    crt_color.b = texture(u_texture, uv - vec2(0.001, 0.0)).b;
    
    // 5. 将计算出的“高光眩光层”叠加到画面中
    crt_color += glow * 1.1; 

    // 6. 动态模拟噪点 
    float grain = pseudo_random(uv + sin(u_time)) * 0.06;
    crt_color += vec3(grain);

    // 7. 电压高频闪烁 (Flicker)
    float flicker = 0.985 + 0.015 * sin(u_time * 50.0) * cos(u_time * 20.0);
    crt_color *= flicker;

    // 8. 保留你满意的单条极细暗带
    float wave = sin(uv.y * 3.14159 - u_time * 0.6);
    float normalized_wave = wave * 0.5 + 0.5;
    float bar_factor = pow(normalized_wave, 128.0);
    float single_bar = mix(1.0, 0.55, bar_factor);
    crt_color *= single_bar;

    // 9. 【横向经典扫描线】
    float scanline = sin(uv.y * 550.0 * 3.14159) * 0.12; 
    crt_color -= vec3(scanline);

    // 10. 【纵向孔径栅格】
    float mask = 0.78 + 0.22 * sin(uv.x * 750.0 * 3.14159);
    crt_color *= mask;

    // 11. 柔和的边缘暗角 (Vignette)
    float vignette = uv.x * uv.y * (1.0 - uv.x) * (1.0 - uv.y);
    vignette = clamp(pow(16.0 * vignette, 0.15), 0.0, 1.0);
    crt_color *= vignette;

    f_color = vec4(crt_color, 1.0);
}
"""

def generate_beep_sound(frequency=1000, sample_rate=44100):
    num_samples = sample_rate
    audio_buffer = array.array('h', [0] * (num_samples * 2))
    for i in range(num_samples):
        t = float(i) / sample_rate
        value = int(32767 * math.sin(2 * math.pi * frequency * t))
        audio_buffer[i * 2] = value      
        audio_buffer[i * 2 + 1] = value  
    return pygame.mixer.Sound(buffer=audio_buffer)


def draw_mute_icon(screen, rect, is_muted):
    cx, cy = rect.center
    body_color = (65, 85, 90) if is_muted else (0, 255, 200)
    pygame.draw.rect(screen, body_color, (cx - 12, cy - 6, 6, 12))
    poly_points = [(cx - 6, cy - 6), (cx, cy - 12), (cx, cy + 12), (cx - 6, cy + 6)]
    pygame.draw.polygon(screen, body_color, poly_points)
    
    if is_muted:
        x_color = (255, 90, 90)
        pygame.draw.line(screen, x_color, (cx + 5, cy - 6), (cx + 15, cy + 6), 2)
        pygame.draw.line(screen, x_color, (cx + 15, cy - 6), (cx + 5, cy + 6), 2)
    else:
        wave_color = (0, 255, 200)
        pygame.draw.arc(screen, wave_color, (cx - 4, cy - 8, 16, 16), -math.pi/3, math.pi/3, 2)
        pygame.draw.arc(screen, wave_color, (cx - 8, cy - 13, 26, 26), -math.pi/3, math.pi/3, 2)


def draw_crt_icon(screen, rect, crt_on):
    cx, cy = rect.center
    color = (0, 255, 200) if crt_on else (65, 85, 90)
    pygame.draw.rect(screen, color, (cx - 14, cy - 10, 28, 20), 2, border_radius=3)
    pygame.draw.rect(screen, color, (cx - 10, cy - 7, 18, 14), 1)
    pygame.draw.line(screen, color, (cx - 6, cy - 10), (cx - 12, cy - 16), 2)
    pygame.draw.line(screen, color, (cx + 6, cy - 10), (cx + 12, cy - 16), 2)


def main():
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=256, allowedchanges=0)
    pygame.init()
    pygame.font.init()

    WIDTH, HEIGHT = 1000, 600
    pygame.display.set_mode((WIDTH, HEIGHT), pygame.OPENGL | pygame.DOUBLEBUF | pygame.HIDDEN)
    
    try:
        window_icon = pygame.image.load("morse.jpg") # 确保你的根目录下有 icon.png
        pygame.display.set_icon(window_icon)
    except Exception as e:
        print(f"未能加载窗口图标: {e}，将使用系统默认图标。")

    pygame.display.set_mode((WIDTH, HEIGHT), pygame.OPENGL | pygame.DOUBLEBUF)
    pygame.display.set_caption("Morse Terminal")

    # =====================================================================
    # 【ModernGL 高级配置：引入 Framebuffer 历史链条】
    # =====================================================================
    ctx = moderngl.create_context()
    pg_surface = pygame.Surface((WIDTH, HEIGHT))
    
    # 编译两个 Shader 程序
    prog_crt = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
    prog_persist = ctx.program(vertex_shader=VERTEX_SHADER, fragment_shader=PERSISTENCE_SHADER)
    
    vertices = array.array('f', [
        -1.0,  1.0,   0.0, 0.0,  
        -1.0, -1.0,   0.0, 1.0,  
         1.0,  1.0,   1.0, 0.0,  
         1.0, -1.0,   1.0, 1.0,  
    ])
    vbo = ctx.buffer(vertices)
    vao_crt = ctx.vertex_array(prog_crt, [(vbo, '2f 2f', 'in_vert', 'in_texcoord')], mode=moderngl.TRIANGLE_STRIP)
    vao_persist = ctx.vertex_array(prog_persist, [(vbo, '2f 2f', 'in_vert', 'in_texcoord')], mode=moderngl.TRIANGLE_STRIP)
    
    # 基础 Pygame 映射纹理
    texture_input = ctx.texture((WIDTH, HEIGHT), 4)
    texture_input.filter = (moderngl.LINEAR, moderngl.LINEAR)

    # 核心：为了实现流畅的流光暂留，建立两个纹理进行帧与帧之间的交替读写（Ping-Pong Buffer）
    persist_tex_A = ctx.texture((WIDTH, HEIGHT), 4)
    persist_tex_B = ctx.texture((WIDTH, HEIGHT), 4)
    for tex in [persist_tex_A, persist_tex_B]:
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        
    # 创建离屏渲染帧缓冲区 (FBO) 用于装载暂留纹理
    fbo_A = ctx.framebuffer(color_attachments=[persist_tex_A])
    fbo_B = ctx.framebuffer(color_attachments=[persist_tex_B])

    # 预先清空缓冲区
    fbo_A.clear(0, 0, 0, 1)
    fbo_B.clear(0, 0, 0, 1)

    # 帧交替状态控制指针
    flip_flop = True

    try:
        font_large = pygame.font.SysFont("Courier New", 54, bold=True)
        font_small = pygame.font.SysFont("Courier New", 26, bold=True)
        font_menu = pygame.font.SysFont("Courier New", 18, bold=True)
    except:
        font_large = pygame.font.SysFont("Arial", 54)   
        font_small = pygame.font.SysFont("Arial", 26)   
        font_menu = pygame.font.SysFont("Arial", 18)   

    beep = generate_beep_sound(frequency=1000, sample_rate=44100)

    is_pressing = False
    press_start_time = 0
    release_start_time = time.time()

    current_code = ""
    decoded_text = ""
    current_wpm = INITIAL_WPM

    is_muted = False
    mute_rect = pygame.Rect(WIDTH - 60, 15, 40, 40) 
    
    crt_on = True 
    crt_rect = pygame.Rect(WIDTH - 110, 15, 40, 40) 

    is_started = False  
    has_broken_char = True
    has_broken_word = True
    scroll_speed = 3  

    dot_duration_history = [INITIAL_DOT_DURATION] * WINDOW_SIZE

    LOW_LEVEL = 390   
    HIGH_LEVEL = 290  
    wave_history = [LOW_LEVEL] * WIDTH

    clock = pygame.time.Clock()
    start_time_stamp = time.time()
    running = True

    while running:
        current_time = time.time()
        FPS = 60

        avg_dot_duration = sum(dot_duration_history) / len(dot_duration_history)
        dash_threshold = avg_dot_duration * 2.0
        char_timeout = avg_dot_duration * 3.0
        word_timeout = avg_dot_duration * 7.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    if mute_rect.collidepoint(event.pos):
                        is_muted = not is_muted
                        if is_muted and is_pressing: beep.stop()
                        elif not is_muted and is_pressing: beep.play(-1)
                    elif crt_rect.collidepoint(event.pos):
                        crt_on = not crt_on

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE and not is_pressing:
                    is_pressing = True
                    is_started = True  
                    press_start_time = current_time
                    if not is_muted: beep.play(-1)
                elif event.key == pygame.K_UP:
                    scroll_speed = min(15, scroll_speed + 1)
                elif event.key == pygame.K_DOWN:
                    scroll_speed = max(1, scroll_speed - 1)
                elif event.key == pygame.K_m:
                    is_muted = not is_muted
                    if is_muted and is_pressing: beep.stop()
                    elif not is_muted and is_pressing: beep.play(-1)
                elif event.key == pygame.K_c:  
                    crt_on = not crt_on

            elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                if is_pressing:
                    is_pressing = False
                    beep.stop()
                    release_start_time = current_time
                    has_broken_char = False
                    has_broken_word = False
                    duration = current_time - press_start_time

                    if duration < dash_threshold:
                        current_code += "."
                    else:
                        current_code += "-"

                    measured_dot = duration if duration < dash_threshold else duration / 3.0
                    measured_dot = max(1.2 / 60, min(1.2 / 5, measured_dot))
                    dot_duration_history.pop(0)
                    dot_duration_history.append(measured_dot)

                    new_avg = sum(dot_duration_history) / len(dot_duration_history)
                    current_wpm = 1.2 / new_avg

                    if current_code == "......":
                        current_code = ""
                        decoded_text = ""
                        has_broken_char = True
                        has_broken_word = True

        if not is_pressing:
            silence_duration = current_time - release_start_time
            if not has_broken_char and silence_duration >= char_timeout:
                if current_code:
                    if current_code in MORSE_DICT:
                        decoded_text += MORSE_DICT[current_code]
                    else:
                        decoded_text += "?"
                    current_code = ""
                has_broken_char = True

            if not has_broken_word and silence_duration >= word_timeout:
                if decoded_text and not decoded_text.endswith(" "):
                    decoded_text += " "
                has_broken_word = True

        for _ in range(scroll_speed):
            wave_history.pop(0)
            wave_history.append(HIGH_LEVEL if is_pressing else LOW_LEVEL)

        # =====================================================================
        # 画面绘制逻辑面
        # =====================================================================
        pg_surface.fill((8, 14, 10))
        
        if decoded_text:
            display_text = decoded_text[-26:]  
        elif not is_started:
            display_text = "Press Space to Start"
        else:
            display_text = ""  
            
        text_surface = font_large.render(display_text, True, (0, 245, 180)) 
        text_rect = text_surface.get_rect(center=(WIDTH // 2, 145))
        pg_surface.blit(text_surface, text_rect)

        code_surface = font_small.render(current_code, True, (0, 255, 200))
        code_rect = code_surface.get_rect(center=(WIDTH // 2, 210))
        pg_surface.blit(code_surface, code_rect)

        square_wave_points = []
        for x in range(WIDTH):
            y = wave_history[x]
            if x > 0 and wave_history[x] != wave_history[x - 1]:
                square_wave_points.append((x, wave_history[x - 1]))
            square_wave_points.append((x, y))

        if len(square_wave_points) > 1:
            pygame.draw.lines(pg_surface, (0, 230, 140), False, square_wave_points, 3)

        wpm_surface = font_small.render(f"WPM: {current_wpm:.1f}", True, (230, 170, 40))
        wpm_rect = wpm_surface.get_rect(bottomright=(WIDTH - 25, HEIGHT - 20))
        pg_surface.blit(wpm_surface, wpm_rect)

        speed_surface = font_small.render(f"Speed: {scroll_speed}", True, (100, 110, 105))
        speed_rect = speed_surface.get_rect(bottomleft=(25, HEIGHT - 20))
        pg_surface.blit(speed_surface, speed_rect)

        menu_items = [
            "[Up/ Down] : Adjust Wave Speed",
            "[M] / Icon : Toggle Mute",
            "[C] / Icon : Toggle CRT Effect",  
            "6 Dots [......] : Clear All"
        ]
        for index, text in enumerate(menu_items):
            item_surface = font_menu.render(text, True, (55, 95, 85))
            pg_surface.blit(item_surface, (20, 18 + index * 24))

        draw_mute_icon(pg_surface, mute_rect, is_muted)
        draw_crt_icon(pg_surface, crt_rect, crt_on)  

        # =====================================================================
        # 【GPU 多级渲染核心：流光暂留处理 + CRT 滤镜】
        # =====================================================================
        # 1. 抓取当前帧原始图像并写入输入纹理
        texture_data = pygame.image.tostring(pg_surface, 'RGBA', True)
        texture_input.write(texture_data)
        
        # 2. 根据状态决定是否通过 Ping-Pong FBO 计算残影
        if crt_on:
            # 荧光暂留衰减率：0.88-0.94之间，值越大，流光尾迹保留越长
            decay_rate = 0.68 
            
            if flip_flop:
                # 绑定 FBO_A 作为输出，读取上一帧的成品 FBO_B 和当前帧输入
                fbo_A.use()
                texture_input.use(0)
                persist_tex_B.use(1)
                prog_persist['u_current_frame'].value = 0
                prog_persist['u_prev_frame'].value = 1
                prog_persist['u_decay'].value = decay_rate
                vao_persist.render()
                
                # 最终显示：把带有流光计算完毕的 FBO_A 送入 CRT 主着色器上屏
                ctx.screen.use()
                persist_tex_A.use(0)
            else:
                # 倒转方向写回 FBO_B
                fbo_B.use()
                texture_input.use(0)
                persist_tex_A.use(1)
                prog_persist['u_current_frame'].value = 0
                prog_persist['u_prev_frame'].value = 1
                prog_persist['u_decay'].value = decay_rate
                vao_persist.render()
                
                ctx.screen.use()
                persist_tex_B.use(0)
                
            flip_flop = not flip_flop # 换向
        else:
            # =====================================================================
            # 【核心修复部分】当 CRT 关闭时，强制让历史缓冲区同步当前干净帧。
            # 将衰减系数 (u_decay) 设为 0.0，使历史遗留瞬间清零，但不停止缓冲区的运转。
            # =====================================================================
            if flip_flop:
                fbo_A.use()
                texture_input.use(0)
                persist_tex_B.use(1)
                prog_persist['u_current_frame'].value = 0
                prog_persist['u_prev_frame'].value = 1
                prog_persist['u_decay'].value = 0.0  # 衰减设为 0，擦除过去的所有强光残留
                vao_persist.render()
                
                ctx.screen.use()
                texture_input.use(0)
            else:
                fbo_B.use()
                texture_input.use(0)
                persist_tex_A.use(1)
                prog_persist['u_current_frame'].value = 0
                prog_persist['u_prev_frame'].value = 1
                prog_persist['u_decay'].value = 0.0  # 同步清空
                vao_persist.render()
                
                ctx.screen.use()
                texture_input.use(0)
                
            flip_flop = not flip_flop

        # 3. 运行主 CRT 显示管模拟
        ctx.clear(0.0, 0.0, 0.0, 1.0)
        if 'u_time' in prog_crt:
            prog_crt['u_time'].value = time.time() - start_time_stamp
        if 'u_crt_on' in prog_crt:
            prog_crt['u_crt_on'].value = crt_on
            
        vao_crt.render()

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
