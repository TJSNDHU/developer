"""Generate 3D bright orange lightning logos for AUREM CTO ProductHunt launch."""
import asyncio
import os
import base64
import sys
from dotenv import load_dotenv
from emergentintegrations.llm.chat import LlmChat, UserMessage

load_dotenv("/app/backend/.env")
API_KEY = os.getenv("EMERGENT_LLM_KEY")
OUT_DIR = "/app/frontend/public/logos"
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "gemini-3.1-flash-image-preview"

PROMPTS = {
    "01_chrome_3d_bolt": (
        "A hyper-realistic 3D rendered logo of a bright glowing orange lightning bolt, "
        "chrome metallic surface with vivid orange glow, floating in space, dramatic studio lighting, "
        "highly polished, octane render, ultra sharp edges, deep orange to bright yellow gradient core, "
        "dark obsidian black background, square 1:1 composition, centered, "
        "no text, no letters, app icon style, ultra detailed, 8k, photorealistic, "
        "rim lighting, glossy reflection, premium tech brand mark."
    ),
    "02_neon_glass_bolt": (
        "3D glass lightning bolt logo, bright neon orange interior glow like molten lava, "
        "transparent glass exterior with refractions, sitting on dark glossy black surface with reflection, "
        "studio lighting, cinematic, octane render, ultra sharp, vibrant tangerine and amber gradient, "
        "1:1 square composition, centered icon, no text, no letters, "
        "premium SaaS app icon, hyper detailed, 8k."
    ),
    "03_isometric_cube_bolt": (
        "3D isometric rounded square app icon, bright orange gradient background "
        "(coral to deep tangerine), with a white embossed lightning bolt extruded forward, "
        "soft drop shadow underneath, subtle highlights and glossy surface, "
        "modern iOS app icon style, premium SaaS branding, "
        "1:1 square, dark background, octane render, ultra sharp, 8k, "
        "no text, no letters, centered."
    ),
    "04_lava_energy_bolt": (
        "A powerful 3D lightning bolt sculpted from molten lava and fire, "
        "bright incandescent orange and yellow, sparks and embers around it, "
        "volumetric glow, energy aura, dark void background, "
        "cinematic render, octane, ultra realistic, dramatic, "
        "1:1 square composition, centered, no text, no letters, app icon style, 8k."
    ),
    "05_holographic_bolt": (
        "3D holographic lightning bolt logo, iridescent orange and gold chrome, "
        "futuristic, glowing edges, slight purple rim light contrast, "
        "floating in dark space, glass refraction, octane render, "
        "1:1 square, no text, no letters, premium AI startup brand mark, "
        "ultra sharp, 8k, cinematic lighting."
    ),
    "06_minimal_3d_emboss": (
        "Minimalist 3D embossed lightning bolt icon, solid bright orange "
        "(hex #ff8a2a to #ff5e1a gradient), soft inner shadow, soft outer glow, "
        "sitting on flat dark charcoal background, ultra clean, "
        "modern fintech/SaaS app icon, 1:1 square, centered, no text, no letters, "
        "octane render, 8k, glossy, premium."
    ),
}


async def gen_one(name: str, prompt: str):
    try:
        chat = LlmChat(
            api_key=API_KEY,
            session_id=f"logo-{name}",
            system_message="You are an expert logo designer creating premium app icons.",
        ).with_model("gemini", MODEL).with_params(modalities=["image", "text"])
        msg = UserMessage(text=prompt)
        text, images = await chat.send_message_multimodal_response(msg)
        if not images:
            print(f"[{name}] no images returned. text={text[:120]}")
            return False
        path = os.path.join(OUT_DIR, f"{name}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(images[0]["data"]))
        print(f"[{name}] saved -> {path}")
        return True
    except Exception as e:
        print(f"[{name}] ERROR: {e}")
        return False


async def main():
    tasks = [gen_one(n, p) for n, p in PROMPTS.items()]
    results = await asyncio.gather(*tasks)
    ok = sum(1 for r in results if r)
    print(f"\nDone: {ok}/{len(PROMPTS)} logos generated.")
    sys.exit(0 if ok > 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
