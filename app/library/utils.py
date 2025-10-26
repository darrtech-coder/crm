import docx
from PyPDF2 import PdfReader

def extract_text(filepath, mime):
    try:
        if mime == "text/plain":
            with open(filepath,"r",encoding="utf-8",errors="ignore") as f:
                return f.read()
        elif mime == "application/pdf":
            pdf=PdfReader(filepath)
            return "\n".join([page.extract_text() or "" for page in pdf.pages])
        elif mime in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document","application/msword"]:
            doc=docx.Document(filepath)
            return "\n".join([p.text for p in doc.paragraphs])
        else:
            return ""
    except Exception as e:
        return ""
