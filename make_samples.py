
import os
from PIL import Image, ImageDraw

os.makedirs("static/img/college", exist_ok=True)
colors = [(230, 90, 90), (90, 230, 90), (90, 90, 230), (230, 230, 90), (230, 90, 230), (90, 230, 230), (150, 150, 150)]

for i in range(1, 8):
    img = Image.new("RGB", (800, 600), color=colors[i-1])
    d = ImageDraw.Draw(img)
    d.text((350, 290), f"Sample Campus {i}\n(Replace in static/img/college/)", fill=(255, 255, 255))
    img.save(f"static/img/college/campus-{i}.jpg")
print("Saved 7 sample images.")
