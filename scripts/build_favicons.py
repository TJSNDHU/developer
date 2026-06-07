"""Generate full favicon set + PH thumbnail from the chosen Molten Glass logo."""
from PIL import Image
import os

SRC = "/app/frontend/public/logos/02_neon_glass_bolt.png"
OUT = "/app/frontend/public"

img = Image.open(SRC).convert("RGBA")
print(f"Source: {img.size}")

# Crop to square (centered) in case it's not perfectly square
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
img = img.crop((left, top, left + side, top + side))
print(f"Square crop: {img.size}")

# Standard favicons (any-purpose — keep dark background, looks crisp)
sizes = {
    "favicon-32.png": 32,
    "favicon-192.png": 192,
    "favicon-512.png": 512,
    "apple-touch-icon.png": 180,
    "logo.png": 1024,                  # canonical brand asset
    "producthunt-logo.png": 240,       # PH thumbnail
    "og-logo.png": 1200,               # OG share
}
for fname, sz in sizes.items():
    resized = img.resize((sz, sz), Image.LANCZOS)
    path = os.path.join(OUT, fname)
    resized.save(path, "PNG", optimize=True)
    print(f"  -> {fname} ({sz}x{sz})")

# favicon.ico (multi-size embedded)
ico_img = img.resize((256, 256), Image.LANCZOS)
ico_img.save(
    os.path.join(OUT, "favicon.ico"),
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("  -> favicon.ico (multi-size)")

# Maskable icon — Android requires 20% safe-zone padding so the logo
# doesn't get clipped by circular/squircle masks. We embed the bolt
# scaled to 60% on a solid dark canvas matching the brand theme.
def make_maskable(size: int, out_name: str):
    canvas = Image.new("RGBA", (size, size), (10, 12, 16, 255))  # #0a0c10
    inner = int(size * 0.6)
    bolt = img.resize((inner, inner), Image.LANCZOS)
    off = (size - inner) // 2
    canvas.paste(bolt, (off, off), bolt)
    canvas.save(os.path.join(OUT, out_name), "PNG", optimize=True)
    print(f"  -> {out_name} ({size}x{size}) maskable")

make_maskable(192, "favicon-192-maskable.png")
make_maskable(512, "favicon-512-maskable.png")

print("\nAll favicons + brand assets generated.")
