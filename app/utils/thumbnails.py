import os, subprocess
from PIL import Image
from pdf2image import convert_from_path

def generate_video_thumbnail(video_path, thumbnail_path):
    """Use ffmpeg to capture a frame from a video."""
    try:
        cmd = [
            "ffmpeg",
            "-ss", "00:00:01",    # grab frame @ 1s
            "-i", video_path,
            "-frames:v", "1",
            "-vf", "scale=320:-1",  # resize width 320, keep aspect
            "-y", thumbnail_path
        ]
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print("Video thumbnail generation failed:", e)
        return False


def generate_image_thumbnail(image_path, thumbnail_path):
    """Scale down an image to max 320px for preview."""
    try:
        with Image.open(image_path) as img:
            img.thumbnail((320, 320))
            img.save(thumbnail_path, "JPEG")
        return True
    except Exception as e:
        print("Image thumbnail generation failed:", e)
        return False


def generate_pdf_thumbnail(pdf_path, thumbnail_path):
    """Render first page of a PDF as thumbnail."""
    try:
        pages = convert_from_path(pdf_path, dpi=100, first_page=1, last_page=1)
        if pages:
            img = pages[0]
            img.thumbnail((320, 320))
            img.save(thumbnail_path, "JPEG")
            return True
        return False
    except Exception as e:
        print("PDF thumbnail generation failed:", e)
        return False



def auto_generate_thumbnail(filepath, mimetype, upload_dir):
    """Return thumbnail filename if generated successfully, else None"""
    thumb_base = os.path.splitext(os.path.basename(filepath))[0] + "_thumb.jpg"
    thumb_path = os.path.join(upload_dir, thumb_base)

    if "video" in mimetype:
        if generate_video_thumbnail(filepath, thumb_path):
            return thumb_base
    elif "image" in mimetype:
        if generate_image_thumbnail(filepath, thumb_path):
            return thumb_base
    elif "pdf" in mimetype:
        if generate_pdf_thumbnail(filepath, thumb_path):
            return thumb_base
    return None