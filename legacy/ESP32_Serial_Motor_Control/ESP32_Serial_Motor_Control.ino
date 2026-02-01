/*
 * ESP32-S3 Motor Control with Serial Commands
 * Motor Driver: A1, A2, B1, B2 connected to GPIO 1, 2, 42, 41
 * Commands: w=forward, s=backward, a=left, d=right, x=stop
 */

// Motor A pins (Left Motor)
#define MOTOR_A1 1
#define MOTOR_A2 2

// Motor B pins (Right Motor)
#define MOTOR_B1 42
#define MOTOR_B2 41

// PWM properties
#define PWM_FREQ 5000
#define PWM_RESOLUTION 8

// Motor speed (0-255)
int motorSpeed = 200;  // Adjust this value for different speeds

void setup() {
  // Initialize serial communication
  Serial.begin(115200);
  delay(1000);
  
  // Configure PWM for each pin (new ESP32 core 3.x API)
  ledcAttach(MOTOR_A1, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(MOTOR_A2, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(MOTOR_B1, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(MOTOR_B2, PWM_FREQ, PWM_RESOLUTION);
  
  // Stop motors initially
  stopMotors();
  
  Serial.println("\n=== ESP32-S3 Motor Control ===");
  Serial.println("Commands:");
  Serial.println("  w - Forward");
  Serial.println("  s - Backward");
  Serial.println("  a - Turn Left");
  Serial.println("  d - Turn Right");
  Serial.println("  x - Stop");
  Serial.println("  + - Increase Speed");
  Serial.println("  - - Decrease Speed");
  Serial.println("  ? - Show current speed");
  Serial.println("==============================\n");
}

void loop() {
  if (Serial.available() > 0) {
    char command = Serial.read();
    
    switch(command) {
      case 'w':
      case 'W':
        moveForward();
        Serial.println("Moving Forward");
        break;
        
      case 's':
      case 'S':
        moveBackward();
        Serial.println("Moving Backward");
        break;
        
      case 'a':
      case 'A':
        turnLeft();
        Serial.println("Turning Left");
        break;
        
      case 'd':
      case 'D':
        turnRight();
        Serial.println("Turning Right");
        break;
        
      case 'x':
      case 'X':
        stopMotors();
        Serial.println("Stopped");
        break;
        
      case '+':
        motorSpeed = min(255, motorSpeed + 25);
        Serial.print("Speed increased to: ");
        Serial.println(motorSpeed);
        break;
        
      case '-':
        motorSpeed = max(0, motorSpeed - 25);
        Serial.print("Speed decreased to: ");
        Serial.println(motorSpeed);
        break;
        
      case '?':
        Serial.print("Current speed: ");
        Serial.println(motorSpeed);
        break;
    }
  }
}

// Motor control functions
void moveForward() {
  // Motor A forward
  ledcWrite(MOTOR_A1, motorSpeed);
  ledcWrite(MOTOR_A2, 0);
  
  // Motor B forward
  ledcWrite(MOTOR_B1, motorSpeed);
  ledcWrite(MOTOR_B2, 0);
}

void moveBackward() {
  // Motor A backward
  ledcWrite(MOTOR_A1, 0);
  ledcWrite(MOTOR_A2, motorSpeed);
  
  // Motor B backward
  ledcWrite(MOTOR_B1, 0);
  ledcWrite(MOTOR_B2, motorSpeed);
}

void turnLeft() {
  // Motor A backward (or stop)
  ledcWrite(MOTOR_A1, 0);
  ledcWrite(MOTOR_A2, motorSpeed);
  
  // Motor B forward
  ledcWrite(MOTOR_B1, motorSpeed);
  ledcWrite(MOTOR_B2, 0);
}

void turnRight() {
  // Motor A forward
  ledcWrite(MOTOR_A1, motorSpeed);
  ledcWrite(MOTOR_A2, 0);
  
  // Motor B backward (or stop)
  ledcWrite(MOTOR_B1, 0);
  ledcWrite(MOTOR_B2, motorSpeed);
}

void stopMotors() {
  // Stop both motors
  ledcWrite(MOTOR_A1, 0);
  ledcWrite(MOTOR_A2, 0);
  ledcWrite(MOTOR_B1, 0);
  ledcWrite(MOTOR_B2, 0);
}