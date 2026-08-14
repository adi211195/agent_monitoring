import subprocess
import sys
import os

def run_command(command):
    try:
        process = subprocess.Popen(command, shell=True)
        process.wait()
        return process.returncode
    except Exception as e:
        print(f"Error executing command: {e}")
        return 1

def main():
    print("========================================")
    print("  Monitoring App - Python Runner")
    print("========================================")
    print()

    # 1. Install dependencies
    print("Checking and installing dependencies...")
    dep_cmd = f"{sys.executable} -m pip install psutil pywin32 requests Pillow mss opencv-python winsdk pynput"
    if run_command(dep_cmd) != 0:
        print("Failed to install dependencies.")
        input("Press Enter to exit...")
        return

    # 2. Run the main app
    print("\nStarting Monitoring Application...")
    app_cmd = f"{sys.executable} main_app.py"
    run_command(app_cmd)

if __name__ == "__main__":
    main()
