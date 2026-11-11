from config import ON_REAL_DRONE, SERVO_LEFT_PIN, SERVO_RIGHT_PIN


class SimHatch:
	def open(self):
		print("[sim hatch] open")
	
	def close(self):
		print("[sim hatch] close")


class ServoHatch:
	def __init__(self):
		import gpiozero
		
		self.left_servo = gpiozero.Servo(SERVO_LEFT_PIN)
		self.right_servo = gpiozero.Servo(SERVO_RIGHT_PIN)
	
	def open(self):
		self.left_servo.min()
		self.right_servo.min()
	
	def close(self):
		self.left_servo.max()
		self.right_servo.max()


def make_hatch():
	if ON_REAL_DRONE:
		return ServoHatch()
	
	return SimHatch()