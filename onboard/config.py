import platform

DRONE_NAME = "megadrone"
MAVLINK_CONNECTION = "tcp:127.0.0.1:5760"
PORT = 8765

SERVO_LEFT_PIN = 17
SERVO_RIGHT_PIN = 18

ON_REAL_DRONE = platform.system() != "Windows"