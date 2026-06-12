import io
import base64
import pdfplumber


def extract_text_from_pdf(pdf_path: str) -> str:
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
            for table in page.extract_tables():
                for row in table:
                    if row:
                        parts.append(" | ".join(str(c) for c in row if c))
    return "\n\n".join(parts)


def pdf_to_images_base64(pdf_path: str, max_pages: int = 3, dpi: int = 150) -> list[str]:
    """Convert first N pages of PDF to base64 JPEG strings for vision models."""
    from pdf2image import convert_from_path

    images = convert_from_path(pdf_path, dpi=dpi, first_page=1, last_page=max_pages)
    result = []
    for img in images:
        # Resize if too wide (Groq vision has size limits)
        if img.width > 1280:
            ratio = 1280 / img.width
            img = img.resize((1280, int(img.height * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        result.append(base64.b64encode(buf.getvalue()).decode())
    return result
