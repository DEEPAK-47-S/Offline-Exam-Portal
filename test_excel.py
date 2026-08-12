
import sys
sys.path.append(".")
from app import read_import_file

class MockFile:
    def __init__(self, filename):
        self.filename = filename
    def read(self):
        return open(self.filename, "rb").read()

print(read_import_file(MockFile("test.xlsx")))
