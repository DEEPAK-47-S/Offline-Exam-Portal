import os, sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app import run_once

python_code = "print('Hello Python')"
java_code = "public class Main { public static void main(String[] args) { System.out.println(\"Hello Java\"); } }"
c_code = "#include <stdio.h>\nint main() { printf(\"Hello C\\n\"); return 0; }"

print("Testing Python:")
success, output, time_ms = run_once("python", python_code, "")
print(f"Success: {success}, Output: {output.strip()}, Time: {time_ms}ms\n")

print("Testing Java:")
success, output, time_ms = run_once("java", java_code, "")
print(f"Success: {success}, Output: {output.strip()}, Time: {time_ms}ms\n")

print("Testing C:")
success, output, time_ms = run_once("c", c_code, "")
print(f"Success: {success}, Output: {output.strip()}, Time: {time_ms}ms\n")
