import contextlib
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from random import random
from typing import Callable, Dict, Literal, Optional, Tuple, Union

import pandas as pd
import pdfplumber
import requests
import streamlit as st
from pdf2docx import Converter
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PdfReadError, PdfStreamError
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from streamlit import session_state
from streamlit.runtime.uploaded_file_manager import UploadedFile
from streamlit_pdf_viewer import pdf_viewer


def select_pages(container, key: str):
    return container.text_input(
        "Pages to extract from?",
        placeholder="all",
        help="""
    Format
    ------
    **all:** all pages  
    **2:** 2nd page  
    **1-3:** pages 1 to 3  
    **2,4:** pages 2 and 4  
    **1-3,5:** pages 1 to 3 and 5""",
        key=key,
    ).lower()


@st.cache_data
def image_to_pdf(stamp_img: Union[Path, str]) -> PdfReader:
    img = Image.open(stamp_img)
    img_as_pdf = BytesIO()
    img.save(img_as_pdf, "pdf")
    return PdfReader(img_as_pdf)


@st.cache_data
def watermark_img(
    reader: PdfReader,
    stamp_img: UploadedFile,
) -> None:
    # Convert the image to a PDF
    stamp_pdf = image_to_pdf(stamp_img)

    # Then use the same stamp code from above
    stamp_page = stamp_pdf.pages[0]

    writer = PdfWriter()

    writer.append(reader)

    for content_page in writer.pages:
        content_page.merge_transformed_page(
            stamp_page, Transformation(), expand=True, over=False
        )

    # TODO: Write to byte_stream
    with open("watermarked.pdf", "wb") as fp:
        writer.write(fp)


def get_option(key: Literal["main", "merge"]) -> str:
    return st.radio(
        label="Upload a PDF, or load PDF from a URL",
        options=(
            "Upload a PDF ⬆️",
            "Load PDF from a URL 🌐",
        ),
        horizontal=True,
        help="PDFs are deleted from the server when you\n"
        "* upload another PDF, or\n"
        "* clear the file uploader, or\n"
        "* close the browser tab.",
        key=f"upload_{key}",
    )


def get_password(key: Literal["main", "merge"]) -> Optional[str]:
    password = st.text_input(
        "PDF Password",
        type="password",
        placeholder="Required if PDF is protected",
        key=f"password_{key}",
    )
    return password if password != "" else None


def upload_pdf(
    key: Literal["main", "merge"], password: Optional[str]
) -> Optional[Tuple[bytes, PdfReader]]:
    if file := st.file_uploader(
        label="Upload a PDF",
        type=["pdf"],
        key=f"file_{key}",
    ):
        session_state["file"] = file
        session_state["name"] = file.name
        pdf = file.getvalue()
        try:
            reader = PdfReader(BytesIO(pdf), password=password)
        except PdfReadError:
            reader = PdfReader(BytesIO(pdf))
        return pdf, reader
    return None, None


def load_pdf_from_url(
    key: Literal["main", "merge"], password: Optional[str]
) -> Optional[Tuple[bytes, PdfReader]]:
    url = st.text_input(
        "PDF URL",
        key=f"url_{key}",
        value="https://getsamplefiles.com/download/pdf/sample-1.pdf",
    )

    @st.cache_data
    def _cached_get_url(url: str) -> requests.Response:
        return requests.get(url)

    if url != "":
        try:
            response = _cached_get_url(url)
            session_state["file"] = pdf = response.content
            session_state["name"] = url.split("/")[-1]
            try:
                reader = PdfReader(BytesIO(pdf), password=password)
            except PdfReadError:
                reader = PdfReader(BytesIO(pdf))
            return pdf, reader
        except PdfStreamError:
            st.error("The URL does not seem to be a valid PDF file.", icon="❌")
    return None, None


def load_pdf(
    key: Literal["main", "merge"] = "main",
) -> Optional[Tuple[bytes, PdfReader, str, bool]]:
    option = get_option(key)
    password = get_password(key)

    # Map options to functions
    option_functions: Dict[str, Callable[[str, str], Tuple[bytes, PdfReader]]] = {
        "Upload a PDF ⬆️": upload_pdf,
        "Load PDF from a URL 🌐": load_pdf_from_url,
    }

    if function := option_functions.get(option):
        pdf, reader = function(key, password)

        if pdf:
            preview_pdf(
                reader,
                pdf,
                key,
                password,
            )
            return pdf, reader, password, reader.is_encrypted

    return None, None, "", False


def handle_encrypted_pdf(reader: PdfReader, password: str, key: str) -> None:
    if password:
        session_state["decrypted_filename"] = f"unprotected_{session_state['name']}"
        decrypt_pdf(
            reader,
            password,
            filename=session_state["decrypted_filename"],
        )
        pdf_viewer(
            f"unprotected_{session_state['name']}",
            height=600 if key == "main" else 250,
            key=str(random()),
        )
    else:
        st.error("Password required", icon="🔒")


def handle_unencrypted_pdf(pdf: bytes, key: str) -> None:
    pdf_viewer(
        pdf,
        height=600 if key == "main" else 250,
        key=str(random()),
    )


def display_metadata(reader: PdfReader) -> None:
    metadata = {"Number of pages": len(reader.pages)}
    for k in reader.metadata:
        value = reader.metadata[k]
        if is_pdf_datetime(value):
            value = convert_pdf_datetime(value)

        metadata[k.replace("/", "")] = value

    metadata = pd.DataFrame.from_dict(metadata, orient="index", columns=["Value"])
    metadata.index.name = "Metadata"

    st.dataframe(metadata)


def preview_pdf(
    reader: PdfReader,
    pdf: bytes = None,
    key: Literal["main", "other"] = "main",
    password: str = "",
) -> None:
    with contextlib.suppress(NameError):
        if key == "main":
            lcol, rcol = st.columns([2, 1])
            with lcol.expander("📄 **Preview**", expanded=bool(pdf)):
                if reader.is_encrypted:
                    handle_encrypted_pdf(reader, password, key)
                else:
                    handle_unencrypted_pdf(pdf, key)

            with rcol.expander("🗄️ **Metadata**"):
                display_metadata(reader)
        elif reader.is_encrypted:
            handle_encrypted_pdf(reader, password, key)
        else:
            handle_unencrypted_pdf(pdf, key)


@st.cache_data
def is_pdf_datetime(s: str) -> bool:
    pattern = r"^D:\d{14}\+\d{2}\'\d{2}\'$"
    return bool(re.match(pattern, s))


@st.cache_data
def convert_pdf_datetime(pdf_datetime: str) -> str:
    # Remove the 'D:' at the beginning
    pdf_datetime = pdf_datetime[2:]

    # Extract the date, time, and timezone components
    date_str = pdf_datetime[:8]
    time_str = pdf_datetime[8:14]
    tz_str = pdf_datetime[14:]

    return (
        datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S").strftime(
            "%Y-%m-%d %H:%M:%S "
        )
        + tz_str
    )


@st.cache_data
def parse_page_numbers(page_numbers_str):
    # Split the input string by comma or hyphen
    parts = page_numbers_str.split(",")

    # Initialize an empty list to store parsed page numbers
    parsed_page_numbers = []

    # Iterate over each part
    for part in parts:
        # Remove any leading/trailing spaces
        part = part.strip()

        # If the part contains a hyphen, it represents a range
        if "-" in part:
            start, end = map(int, part.split("-"))
            parsed_page_numbers.extend(range(start, end + 1))
        else:
            # Otherwise, it's a single page number
            parsed_page_numbers.append(int(part))

    return [i - 1 for i in parsed_page_numbers]


def extract_text(
    reader: PdfReader.pages,
    page_numbers_str: str = "all",
    mode: Literal["plain", "layout"] = "plain",
) -> str:
    text = ""

    if page_numbers_str == "all":
        for page in reader.pages:
            text = text + " " + page.extract_text(extraction_mode=mode)
    else:
        pages = parse_page_numbers(page_numbers_str)
        for page in pages:
            text = text + " " + reader.pages[page].extract_text()

    return text


def extract_images(reader: PdfReader.pages, page_numbers_str: str = "all") -> str:
    images = {}
    if page_numbers_str == "all":
        for page in reader.pages:
            images |= {image.data: image.name for image in page.images}

    else:
        pages = parse_page_numbers(page_numbers_str)
        for page in pages:
            images.update(
                {image.data: image.name for image in reader.pages[page].images}
            )

    return images


def extract_tables(file, page_numbers_str):
    st.caption(
        "Adjust vertical and horizontal strategies for better extraction. Read details about the strategies [here](https://github.com/jsvine/pdfplumber?tab=readme-ov-file#table-extraction-strategies)."
    )
    col0, col1 = st.columns(2)
    vertical_strategy = col0.selectbox(
        "Vertical strategy",
        ["lines", "lines_strict", "text"],
        index=2,
    )
    horizontal_strategy = col1.selectbox(
        "Horizontal strategy",
        ["lines", "lines_strict", "text"],
        index=2,
    )

    header = st.checkbox("Header")

    first_row_index = 1 if header else 0

    with pdfplumber.open(
        BytesIO(file) if isinstance(file, bytes) else file,
        password=session_state["password"],
    ) as table_pdf:
        if page_numbers_str == "all":
            for page in table_pdf.pages:
                for table in page.extract_tables(
                    {
                        "vertical_strategy": vertical_strategy,
                        "horizontal_strategy": horizontal_strategy,
                    }
                ):
                    st.write(
                        pd.DataFrame(
                            table[first_row_index:],
                            columns=table[0] if header else None,
                        )
                    )
        else:
            pages = parse_page_numbers(page_numbers_str)
            for page in pages:
                for page in table_pdf.pages[page : page + 1]:
                    for table in page.extract_tables(
                        {
                            "vertical_strategy": vertical_strategy,
                            "horizontal_strategy": horizontal_strategy,
                        }
                    ):
                        st.write(
                            pd.DataFrame(
                                table[first_row_index:],
                                columns=table[0] if header else None,
                            )
                        )


def decrypt_pdf(reader: PdfReader, password: str, filename: str) -> None:
    reader.decrypt(password)

    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    with open(filename, "wb") as f:
        writer.write(f)


@st.cache_data
def remove_images(pdf: bytes, remove_images: bool, password: str) -> bytes:
    reader = PdfReader(BytesIO(pdf))

    if reader.is_encrypted:
        reader.decrypt(password)

    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    writer.add_metadata(reader.metadata)

    if remove_images:
        writer.remove_images()

    bytes_stream = BytesIO()
    writer.write(bytes_stream)

    bytes_stream.seek(0)

    return bytes_stream.getvalue()


def reduce_image_quality(pdf: bytes, quality: int, password: str) -> bytes:
    reader = PdfReader(BytesIO(pdf))

    if reader.is_encrypted:
        reader.decrypt(password)

    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    writer.add_metadata(reader.metadata)

    for page in writer.pages:
        for img in page.images:
            img.replace(img.image, quality=quality)

    bytes_stream = BytesIO()
    writer.write(bytes_stream)

    bytes_stream.seek(0)

    return bytes_stream.getvalue()


@st.cache_data
def compress_pdf(pdf: bytes, password: str) -> bytes:
    reader = PdfReader(BytesIO(pdf))

    if reader.is_encrypted:
        reader.decrypt(password)

    writer = PdfWriter(clone_from=reader)

    for page in writer.pages:
        page.compress_content_streams()  # This is CPU intensive!

    bytes_stream = BytesIO()
    writer.write(bytes_stream)
    bytes_stream.seek(0)

    return bytes_stream.getvalue()


@st.cache_data
def convert_pdf_to_word(pdf):
    cv = Converter(stream=pdf, password=session_state.password)
    docx_stream = BytesIO()
    cv.convert(docx_stream, start=0, end=None)
    cv.close()

    docx_stream.seek(0)
    return docx_stream


def hex_to_rgba(hex_color: str) -> Tuple[float, float, float]:
    """
    Convert a hexadecimal color code to an RGB color tuple.

    Args:
        hex_color (str): The hexadecimal color code.

    Returns:
        Tuple[float, float, float]: The RGB color tuple
    """
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))


def draw_watermark_grid(
    can, stamp_label: str, step_x: int, step_y: int, width: float, height: float
) -> None:
    """
    Draw a grid of watermarks on the given canvas.

    Args:
        can (canvas.Canvas): The canvas to draw the watermarks on.
        stamp_label (str): The label to be displayed as the watermark.
        step_x (int): The horizontal spacing between watermarks.
        step_y (int): The vertical spacing between watermarks.
        width (float): The width of the canvas.
        height (float): The height of the canvas.

    Returns:
        None
    """
    for x in range(-100, int(width) + 100, step_x):
        for y in range(-100, int(height) + 100, step_y):
            can.saveState()
            can.translate(x, y)
            can.rotate(45)
            can.drawCentredString(0, 0, stamp_label)
            can.restoreState()


def merge_watermark_into_pdf(pdf: bytes, watermark: BytesIO) -> bytes:
    """
    Merge a watermark into a PDF document.

    Args:
        pdf (bytes): The PDF document to merge the watermark into.
        watermark (BytesIO): The watermark to merge into the PDF.

    Returns:
        bytes: The merged PDF document.
    """
    writer = PdfWriter()
    reader = PdfReader(BytesIO(pdf))
    watermark_reader = PdfReader(watermark)
    watermark_page = watermark_reader.pages[0]
    for page in reader.pages:
        page.merge_page(watermark_page, over=False)
        writer.add_page(page)
    with BytesIO() as fp:
        writer.write(fp)
        fp.seek(0)
        return fp.read()


def create_watermark_canvas(
    stamp_label: str, stamp_size: int, stamp_color: str, stamp_transparency: float
) -> BytesIO:
    """
    Create a watermark canvas with the given label, size, color, and transparency.

    Args:
        stamp_label (str): The label to be displayed as the watermark.
        stamp_size (int): The font size of the watermark.
        stamp_color (str): The color of the watermark in hexadecimal format.
        stamp_transparency (float): The transparency of the watermark.

    Returns:
        BytesIO: A BytesIO object containing the watermark canvas.
    """
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)
    can.setFont("Helvetica", stamp_size)
    color = hex_to_rgba(stamp_color)
    can.setFillColorRGB(*color)
    can.setFillAlpha(stamp_transparency)
    can.saveState()
    draw_watermark_grid(
        can, stamp_label, step_x=150, step_y=100, width=letter[0], height=letter[1]
    )
    can.save()
    packet.seek(0)
    return packet


@st.cache_data
def watermark_pdf(
    pdf: bytes,
    stamp_label: str,
    stamp_size: int,
    stamp_color: str,
    stamp_transparency: float,
) -> bytes:
    watermark = create_watermark_canvas(
        stamp_label, stamp_size, stamp_color, stamp_transparency
    )
    return merge_watermark_into_pdf(pdf, watermark)
