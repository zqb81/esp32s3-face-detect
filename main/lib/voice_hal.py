# voice_hal.py — ESP32-S3-N16R8 硬件抽象层
# 对齐 C 版本 device_ctrl.c / voice_task.c
#
# ESP32-S3-WROOM-1 N16R8 可用 GPIO：0, 38, 46, 48
#   GPIO 38  → WS2812 RGB 灯带  (对应 C: DEVICE_RGB_PIN=38)
#   GPIO 48  → 蜂鸣器 PWM       (对应 C: DEVICE_BUZZER_PIN=48)
#   GPIO 22-25 模块未引出，GPIO 26-37 Flash/PSRAM 占用
#
# Motor / LED / DHT / Light：与 C 版本一致，保留空实现（no free GPIO）

import gc
import utime
from machine import Pin, I2S, PWM

# ===== I2S 麦克风 INMP441 (对应 C: I2S_MIC_SCK/WS/SD) =====
MIC_SCK    = 39
MIC_WS     = 40
MIC_SD     = 41
MIC_I2S_ID = 0

# ===== I2S 功放 MAX98357A (对应 C: I2S_SPK_BCLK/LRC/DIN) =====
# GPIO 43/44 = UART0 TX/RX，需 USB-CDC REPL 固件
SPK_SCK    = 42
SPK_WS     = 43
SPK_SD     = 44
SPK_I2S_ID = 1

# ===== 音频参数 =====
SAMPLE_RATE    = 16000
BITS           = 16
BUF_SIZE       = 4096
MAX_RECORD_SEC = 10

# ===== 语音按键 (对应 C: VOICE_BTN_GPIO=45) =====
# GPIO 45 是 strapping 引脚，PULL_UP 保高电平，勿在上电时按住
BUTTON_PIN = 45

# ===== 设备控制 (对应 C: DEVICE_RGB_PIN=38 / DEVICE_BUZZER_PIN=48) =====
RGB_PIN    = 38   # WS2812 NeoPixel
RGB_NUM    = 5
BUZZER_PIN = 48   # 有源蜂鸣器 PWM
BUZZER_FREQ    = 1000
BUZZER_ON_MS   = 200
BUZZER_OFF_MS  = 200
MOTOR_DUTY     = 0   # Motor 无 GPIO，仅占位

# ===== I2S 实例 =====
_mic_i2s = None
_spk_i2s = None


def init_mic():
    global _mic_i2s
    if _mic_i2s is not None:
        try: _mic_i2s.deinit()
        except Exception: pass
    _mic_i2s = I2S(MIC_I2S_ID,
                   sck=Pin(MIC_SCK), ws=Pin(MIC_WS), sd=Pin(MIC_SD),
                   mode=I2S.RX, bits=BITS, format=I2S.MONO,
                   rate=SAMPLE_RATE, ibuf=BUF_SIZE)
    return _mic_i2s


def deinit_mic():
    global _mic_i2s
    if _mic_i2s:
        try: _mic_i2s.deinit()
        except Exception: pass
        _mic_i2s = None


def mic_read(buf):
    return _mic_i2s.readinto(buf) if _mic_i2s else 0


def init_speaker():
    global _spk_i2s
    if _spk_i2s is not None:
        try: _spk_i2s.deinit()
        except Exception: pass
    _spk_i2s = I2S(SPK_I2S_ID,
                   sck=Pin(SPK_SCK), ws=Pin(SPK_WS), sd=Pin(SPK_SD),
                   mode=I2S.TX, bits=BITS, format=I2S.MONO,
                   rate=SAMPLE_RATE, ibuf=BUF_SIZE)
    return _spk_i2s


def deinit_speaker():
    global _spk_i2s
    if _spk_i2s:
        try: _spk_i2s.deinit()
        except Exception: pass
        _spk_i2s = None


def speaker_write(data):
    return _spk_i2s.write(data) if _spk_i2s else 0


# ===== 按键（中断 + 消抖，对应 C: btn_isr）=====
_btn_pin = None
btn_pressed        = False
btn_released       = False
btn_press_start_ms = 0
btn_last_irq_ms    = 0
_DEBOUNCE_MS = 200


def _btn_irq_handler(pin):
    global btn_pressed, btn_released, btn_press_start_ms, btn_last_irq_ms
    now = utime.ticks_ms()
    if utime.ticks_diff(now, btn_last_irq_ms) < _DEBOUNCE_MS:
        return
    btn_last_irq_ms = now
    if pin.value() == 0:
        btn_pressed = True
        btn_released = False
        btn_press_start_ms = now
    else:
        btn_pressed = False
        if utime.ticks_diff(now, btn_press_start_ms) >= 100:
            btn_released = True


def init_button():
    global _btn_pin
    _btn_pin = Pin(BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    _btn_pin.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING,
                 handler=_btn_irq_handler)


def consume_button_release():
    global btn_released
    if btn_released:
        btn_released = False
        return True
    return False


def clear_button_state():
    global btn_pressed, btn_released
    btn_pressed = btn_released = False


# ===== Motor — stub (no free GPIO，对应 C: device_motor_on/off) =====
def motor_on(speed=MOTOR_DUTY):
    print("[HW] Motor not available (no free GPIO)")

def motor_off():
    pass


# ===== LED — stub (no free GPIO，对应 C: device_led_on/off) =====
def led_on():
    print("[HW] LED not available (no free GPIO)")

def led_off():
    pass


# ===== 蜂鸣器 PWM (GPIO 48，对应 C: device_buzzer_*) =====
_buzzer_pwm = None


def init_buzzer():
    global _buzzer_pwm
    _buzzer_pwm = PWM(Pin(BUZZER_PIN), freq=BUZZER_FREQ)
    _buzzer_pwm.duty(0)


def buzzer_on():
    if _buzzer_pwm:
        _buzzer_pwm.freq(BUZZER_FREQ)
        _buzzer_pwm.duty(512)


def buzzer_off():
    if _buzzer_pwm:
        _buzzer_pwm.duty(0)


def buzzer_beep(times=3):
    for _ in range(times):
        buzzer_on()
        utime.sleep_ms(BUZZER_ON_MS)
        buzzer_off()
        utime.sleep_ms(BUZZER_OFF_MS)


# ===== RGB WS2812 NeoPixel (GPIO 38，对应 C: device_rgb_*) =====
_np = None
rgb_brightness   = 100
_last_static_rgb = (0, 0, 10)


def init_rgb():
    global _np
    try:
        import neopixel
        _np = neopixel.NeoPixel(Pin(RGB_PIN), RGB_NUM)
        rgb_set(0, 0, 10)   # 开机待机蓝
    except Exception as e:
        print("[WARN] NeoPixel init failed:", e)
        _np = None


def rgb_set(r, g, b):
    if _np:
        f = rgb_brightness / 100.0
        c = (int(r * f), int(g * f), int(b * f))
        for i in range(len(_np)):
            _np[i] = c
        _np.write()


def rgb_off():
    rgb_set(0, 0, 0)


def rgb_color(name):
    """对应 C: device_rgb_color()"""
    colors = {
        "idle":   (0, 0, 10),
        "red":    (255, 0, 0),   "green":  (0, 255, 0),
        "blue":   (0, 0, 255),   "yellow": (255, 255, 0),
        "purple": (128, 0, 128), "white":  (255, 255, 255),
        "off":    (0, 0, 0),
    }
    r, g, b = colors.get(name, (0, 0, 0))
    global _last_static_rgb
    _last_static_rgb = (r, g, b)
    rgb_set(r, g, b)


def set_rgb_static(r, g, b):
    global _last_static_rgb
    _last_static_rgb = (r, g, b)
    rgb_set(r, g, b)


def set_rgb_brightness(val):
    global rgb_brightness
    rgb_brightness = max(0, min(100, int(val)))
    rgb_set(_last_static_rgb[0], _last_static_rgb[1], _last_static_rgb[2])


# ===== 一键初始化（对应 C: device_ctrl_init_all）=====
def init_all():
    print("[HW] Init: buzzer=GPIO%d, RGB=GPIO%d (motor/LED/DHT/light disabled)"
          % (BUZZER_PIN, RGB_PIN))
    init_button()
    init_buzzer()
    init_rgb()
    print("[HW] Device control ready")
    gc.collect()
