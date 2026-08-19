import os
import io
import uuid
import mimetypes

from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Depends,
    HTTPException,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

from supabase import create_client, Client

from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials,
)

from auth import (
    verify_password,
    create_token,
    verify_token,
)


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is missing from .env"
    )

if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_KEY is missing from .env"
    )


# ============================================================
# SUPABASE
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Personal File Hub API",
    description="Personal cloud storage and online file editor",
    version="1.0.0"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ============================================================
# STORAGE
# ============================================================

BUCKET_NAME = "user-files"


# ============================================================
# AUTHENTICATION
# ============================================================

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    )
):

    token = credentials.credentials

    payload = verify_token(token)

    if not payload:

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    user_id = payload.get("user_id")

    if user_id is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )

    return int(user_id)


# ============================================================
# REQUEST MODELS
# ============================================================

class LoginRequest(BaseModel):

    username: str
    password: str


class UpdateFileRequest(BaseModel):

    content: str


class RenameFileRequest(BaseModel):

    filename: str


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    return {
        "success": True,
        "message": "Personal File Hub API is working!"
    }


# ============================================================
# LOGIN
# ============================================================

@app.post("/login")
async def login(
    request: LoginRequest
):

    result = (
        supabase
        .table("users")
        .select("*")
        .eq(
            "username",
            request.username
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    user = result.data[0]

    password_valid = verify_password(
        request.password,
        user["password_hash"]
    )

    if not password_valid:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    token = create_token(
        user["id"]
    )

    return {
        "success": True,
        "token": token
    }


# ============================================================
# LIST FILES
# ============================================================

@app.get("/files")
async def list_files(
    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select(
            "id,user_id,filename,size,storage_path,created_at"
        )
        .eq(
            "user_id",
            user_id
        )
        .order(
            "created_at",
            desc=True
        )
        .execute()
    )

    return {
        "success": True,
        "files": result.data
    }


# ============================================================
# GET TEXT FILE CONTENT
# ============================================================

@app.get("/files/{file_id}")
async def get_file(
    file_id: int,

    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    try:

        file_bytes = (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .download(
                file_record["storage_path"]
            )
        )

    except Exception as e:

        print(
            "Storage download error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not read file"
        )

    try:

        content = file_bytes.decode(
            "utf-8"
        )

    except UnicodeDecodeError:

        raise HTTPException(
            status_code=400,
            detail="This file is not a text file"
        )

    return {
        "success": True,
        "id": file_record["id"],
        "filename": file_record["filename"],
        "content": content
    }


# ============================================================
# RAW FILE
#
# Used by:
#   Images
#   PDFs
#   DOCX
#   PPTX
#   Other binary files
# ============================================================

@app.get("/files/{file_id}/raw")
async def get_raw_file(
    file_id: int,

    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    try:

        file_bytes = (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .download(
                file_record["storage_path"]
            )
        )

    except Exception as e:

        print(
            "Raw storage download error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not read file"
        )

    filename = file_record["filename"]

    extension = ""

    if "." in filename:

        extension = (
            filename
            .lower()
            .rsplit(".", 1)[-1]
        )


    # --------------------------------------------------------
    # MIME TYPES
    # --------------------------------------------------------

    mime_types = {

        # Images

        "png":
            "image/png",

        "jpg":
            "image/jpeg",

        "jpeg":
            "image/jpeg",

        "gif":
            "image/gif",

        "webp":
            "image/webp",

        "svg":
            "image/svg+xml",

        "bmp":
            "image/bmp",

        "ico":
            "image/x-icon",

        # PDF

        "pdf":
            "application/pdf",

        # Microsoft Word

        "docx":
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",

        # Microsoft PowerPoint

        "pptx":
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",

        # Text

        "txt":
            "text/plain",

        "html":
            "text/html",

        "htm":
            "text/html",

        "css":
            "text/css",

        "js":
            "text/javascript",

        "json":
            "application/json",

        "xml":
            "application/xml",

        # Archives

        "zip":
            "application/zip",

    }


    media_type = mime_types.get(
        extension,
        mimetypes.guess_type(
            filename
        )[0]
        or "application/octet-stream"
    )


    return StreamingResponse(

        io.BytesIO(
            file_bytes
        ),

        media_type=media_type,

        headers={
            "Content-Disposition":
                (
                    "inline; "
                    f'filename="{filename}"'
                ),

            "X-File-Name":
                filename
        }
    )


# ============================================================
# SAVE / UPDATE FILE
# ============================================================

@app.put("/files/{file_id}")
async def update_file(
    file_id: int,

    request: UpdateFileRequest,

    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    storage_path = file_record[
        "storage_path"
    ]

    file_bytes = request.content.encode(
        "utf-8"
    )

    try:

        (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .update(
                storage_path,
                file_bytes,
                {
                    "content-type":
                        "text/plain",

                    "upsert":
                        "true"
                }
            )
        )

    except Exception as e:

        print(
            "Storage update error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not update file"
        )

    try:

        (
            supabase
            .table("files")
            .update(
                {
                    "size":
                        len(file_bytes)
                }
            )
            .eq(
                "id",
                file_id
            )
            .eq(
                "user_id",
                user_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "Database update error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not update file metadata"
        )

    return {
        "success": True,
        "message":
            "File saved successfully"
    }


# ============================================================
# RENAME FILE
# ============================================================

@app.put("/files/{file_id}/rename")
async def rename_file(
    file_id: int,

    request: RenameFileRequest,

    user_id: int = Depends(
        get_current_user
    )
):

    new_filename = (
        request.filename.strip()
    )

    if not new_filename:

        raise HTTPException(
            status_code=400,
            detail="Filename cannot be empty"
        )

    if (
        "/" in new_filename
        or "\\" in new_filename
    ):

        raise HTTPException(
            status_code=400,
            detail="Invalid filename"
        )

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    old_path = file_record[
        "storage_path"
    ]

    parts = old_path.rsplit(
        "/",
        1
    )

    if len(parts) != 2:

        raise HTTPException(
            status_code=500,
            detail="Invalid storage path"
        )

    directory = parts[0]

    old_storage_filename = parts[1]

    unique_prefix = (
        old_storage_filename
        .split("_", 1)[0]
    )

    new_storage_path = (
        f"{directory}/"
        f"{unique_prefix}_"
        f"{new_filename}"
    )

    try:

        (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .move(
                old_path,
                new_storage_path
            )
        )

    except Exception as e:

        print(
            "Storage rename error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not rename file"
        )

    try:

        (
            supabase
            .table("files")
            .update(
                {
                    "filename":
                        new_filename,

                    "storage_path":
                        new_storage_path
                }
            )
            .eq(
                "id",
                file_id
            )
            .eq(
                "user_id",
                user_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "Database rename error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="File renamed but database update failed"
        )

    return {
        "success": True,
        "message":
            "File renamed successfully",
        "filename":
            new_filename
    }


# ============================================================
# DELETE FILE
# ============================================================

@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int,

    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    storage_path = file_record[
        "storage_path"
    ]

    try:

        (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .remove(
                [storage_path]
            )
        )

    except Exception as e:

        print(
            "Storage delete error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not delete file from storage"
        )

    try:

        (
            supabase
            .table("files")
            .delete()
            .eq(
                "id",
                file_id
            )
            .eq(
                "user_id",
                user_id
            )
            .execute()
        )

    except Exception as e:

        print(
            "Database delete error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Database deletion failed"
        )

    return {
        "success": True,
        "message":
            "File deleted successfully"
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),

    user_id: int = Depends(
        get_current_user
    )
):

    if not file.filename:

        raise HTTPException(
            status_code=400,
            detail="Filename is required"
        )

    file_bytes = await file.read()

    if not file_bytes:

        raise HTTPException(
            status_code=400,
            detail="File is empty"
        )

    unique_id = str(
        uuid.uuid4()
    )

    storage_path = (
        f"{user_id}/"
        f"{unique_id}_"
        f"{file.filename}"
    )

    content_type = (
        file.content_type
        or "application/octet-stream"
    )

    try:

        (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .upload(
                storage_path,
                file_bytes,
                {
                    "content-type":
                        content_type,

                    "upsert":
                        "false"
                }
            )
        )

    except Exception as e:

        print(
            "Storage upload error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not upload file"
        )

    try:

        result = (
            supabase
            .table("files")
            .insert(
                {
                    "user_id":
                        user_id,

                    "filename":
                        file.filename,

                    "storage_path":
                        storage_path,

                    "size":
                        len(file_bytes)
                }
            )
            .execute()
        )

    except Exception as e:

        print(
            "Database insert error:",
            e
        )

        try:

            (
                supabase
                .storage
                .from_(BUCKET_NAME)
                .remove(
                    [storage_path]
                )
            )

        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail="Could not save file information"
        )

    saved_file = (
        result.data[0]
        if result.data
        else None
    )

    return {
        "success": True,

        "message":
            "File uploaded successfully",

        "file":
            saved_file
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/download/{file_id}")
async def download_file(
    file_id: int,

    user_id: int = Depends(
        get_current_user
    )
):

    result = (
        supabase
        .table("files")
        .select("*")
        .eq(
            "id",
            file_id
        )
        .eq(
            "user_id",
            user_id
        )
        .execute()
    )

    if not result.data:

        raise HTTPException(
            status_code=404,
            detail="File not found"
        )

    file_record = result.data[0]

    try:

        file_bytes = (
            supabase
            .storage
            .from_(BUCKET_NAME)
            .download(
                file_record["storage_path"]
            )
        )

    except Exception as e:

        print(
            "Storage download error:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Could not download file"
        )

    return StreamingResponse(

        io.BytesIO(
            file_bytes
        ),

        media_type=
            "application/octet-stream",

        headers={
            "Content-Disposition":
                (
                    "attachment; "
                    f'filename="{file_record["filename"]}"'
                )
        }
    )