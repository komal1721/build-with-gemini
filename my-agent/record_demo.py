import asyncio
import os
import shutil
import numpy as np
from scipy.io import wavfile
from playwright.async_api import async_playwright
from moviepy import VideoFileClip, AudioFileClip

ARTIFACT_DIR = "/config/.gemini/antigravity/brain/eb6baabd-2622-4e59-b34a-9bb16bd16918"

def generate_lofi_music(output_path="lofi_track.wav", duration=25.0):
    sample_rate = 44100
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    chords = [
        [261.63, 329.63, 392.00, 493.88], # Cmaj7
        [220.00, 261.63, 329.63, 392.00], # Am7
        [146.83, 174.61, 220.00, 261.63], # Dm7
        [196.00, 246.94, 293.66, 349.23], # G7
    ]

    audio = np.zeros_like(t)
    chord_duration = 3.0

    for i, time_val in enumerate(t):
        chord_idx = int((time_val % (len(chords) * chord_duration)) // chord_duration)
        current_chord = chords[chord_idx]
        
        # Soft warm synth pad
        chord_wave = sum(np.sin(2 * np.pi * f * time_val) * 0.1 for f in current_chord)
        
        # Upbeat lo-fi drum beat (90 BPM)
        beat_pos = (time_val % 0.666) / 0.666
        
        # Kick drum drop
        kick = np.sin(2 * np.pi * 65 * np.exp(-beat_pos * 18) * time_val) * np.exp(-beat_pos * 12) * 0.25
        
        # Soft snare / hi-hat
        snare = 0.0
        if 0.5 <= beat_pos < 0.7:
            snare_pos = (beat_pos - 0.5) / 0.2
            snare = np.random.normal(0, 0.12) * np.exp(-snare_pos * 15)
            
        hihat = np.random.normal(0, 0.04) * (1.0 if (beat_pos % 0.25) < 0.05 else 0.0)

        audio[i] = chord_wave + kick + snare + hihat

    audio = audio / np.max(np.abs(audio)) * 0.7
    audio_int16 = (audio * 32767).astype(np.int16)
    wavfile.write(output_path, sample_rate, audio_int16)
    print(f"Generated upbeat lo-fi track: {output_path}")

async def record_playwright_demo():
    os.makedirs("video_temp", exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            record_video_dir="video_temp",
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        
        print("Navigating to http://localhost:8080...")
        await page.goto("http://localhost:8080")
        await page.wait_for_timeout(2000)

        # 1. Click example prompt (pantry lookup)
        print("Clicking 'List pantry items' prompt...")
        await page.click("button:has-text('List pantry items')")
        
        # Wait for 1st agent reply bubble
        await page.wait_for_selector(".msg.agent", timeout=30000)
        await page.wait_for_timeout(3500)

        # 2. Ask second richer prompt (Image generation tool call)
        rich_prompt = "Generate a photo of fresh basil and tomato pasta using my pantry items"
        print(f"Sending rich prompt: {rich_prompt}")
        await page.fill("#input", rich_prompt)
        await page.wait_for_timeout(800)
        await page.click("form button[type='submit']")

        # Wait for 2nd agent reply containing image or rich card
        await page.wait_for_selector(".msg.agent:nth-of-type(2)", timeout=60000)
        await page.wait_for_timeout(5000)

        video_path = await page.video.path()
        await context.close()
        await browser.close()
        print(f"Recorded Playwright video: {video_path}")
        return video_path

def merge_video_audio(raw_video_path, audio_path, output_mp4="demo_video.mp4"):
    print("Merging video and upbeat lo-fi audio...")
    video_clip = VideoFileClip(raw_video_path)
    audio_clip = AudioFileClip(audio_path).subclipped(0, video_clip.duration)
    
    final_clip = video_clip.with_audio(audio_clip)
    final_clip.write_videofile(
        output_mp4,
        codec="libx264",
        audio_codec="aac",
        fps=24,
        logger=None
    )
    video_clip.close()
    audio_clip.close()
    print(f"Successfully generated demo video: {output_mp4}")
    
    # Copy to artifacts directory
    artifact_target = os.path.join(ARTIFACT_DIR, "demo_video.mp4")
    shutil.copy(output_mp4, artifact_target)
    print(f"Copied demo video to artifact: {artifact_target}")
    return artifact_target

async def main():
    generate_lofi_music("lofi_track.wav", duration=30.0)
    raw_video = await record_playwright_demo()
    final_video = merge_video_audio(raw_video, "lofi_track.wav", "demo_video.mp4")
    print(f"Demo video recording complete: {final_video}")

if __name__ == "__main__":
    asyncio.run(main())
