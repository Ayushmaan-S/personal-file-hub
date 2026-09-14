import os
import io
import uuid
import mimetypes

from dotenv import load_dotenv
from fastapi import (
    FastAPI, UploadFile, File, Form, Depends, HTTPException
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from supabase import create_client, Client

from auth import verify_password, create_token, verify_token

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUCKET_NAME = "user-files"

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")
if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is missing")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="Personal File Hub API",
    description="Personal cloud storage and online file editor",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    payload = verify_token(credentials.credentials)
    if not payload or payload.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return int(payload["user_id"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UpdateFileRequest(BaseModel):
    content: str


class RenameFileRequest(BaseModel):
    filename: str


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: int | None = None


class RenameFolderRequest(BaseModel):
    name: str


class MoveFileRequest(BaseModel):
    folder_id: int | None = None


def clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid name")
    return name


def verify_folder(folder_id: int | None, user_id: int):
    if folder_id is None:
        return

    result = (
        supabase.table("folders")
        .select("id")
        .eq("id", folder_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Folder not found")


@app.get("/")
async def root():
    return {"success": True, "message": "Personal File Hub API is working!"}


@app.post("/login")
async def login(request: LoginRequest):
    result = (
        supabase.table("users")
        .select("*")
        .eq("username", request.username)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user = result.data[0]

    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"success": True, "token": create_token(user["id"])}


# ============================================================
# FOLDERS
# ============================================================

@app.get("/folders")
async def list_folders(
    parent_id: int | None = None,
    user_id: int = Depends(get_current_user)
):
    query = (
        supabase.table("folders")
        .select("id,user_id,name,parent_id,created_at,updated_at")
        .eq("user_id", user_id)
    )

    if parent_id is None:
        query = query.is_("parent_id", "null")
    else:
        query = query.eq("parent_id", parent_id)

    result = query.order("name", desc=False).execute()

    return {"success": True, "folders": result.data}


@app.get("/folders/all")
async def list_all_folders(
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("folders")
        .select("id,user_id,name,parent_id,created_at,updated_at")
        .eq("user_id", user_id)
        .order("name", desc=False)
        .execute()
    )
    return {"success": True, "folders": result.data}


@app.post("/folders")
async def create_folder(
    request: CreateFolderRequest,
    user_id: int = Depends(get_current_user)
):
    name = clean_name(request.name)
    verify_folder(request.parent_id, user_id)

    # Friendly duplicate check.
    query = (
        supabase.table("folders")
        .select("id")
        .eq("user_id", user_id)
        .eq("name", name)
    )
    if request.parent_id is None:
        query = query.is_("parent_id", "null")
    else:
        query = query.eq("parent_id", request.parent_id)

    existing = query.execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="A folder with this name already exists")

    result = (
        supabase.table("folders")
        .insert({
            "user_id": user_id,
            "name": name,
            "parent_id": request.parent_id
        })
        .execute()
    )

    return {
        "success": True,
        "folder": result.data[0] if result.data else None
    }


@app.put("/folders/{folder_id}/rename")
async def rename_folder(
    folder_id: int,
    request: RenameFolderRequest,
    user_id: int = Depends(get_current_user)
):
    name = clean_name(request.name)

    result = (
        supabase.table("folders")
        .select("*")
        .eq("id", folder_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Folder not found")

    folder = result.data[0]

    query = (
        supabase.table("folders")
        .select("id")
        .eq("user_id", user_id)
        .eq("name", name)
        .neq("id", folder_id)
    )
    if folder["parent_id"] is None:
        query = query.is_("parent_id", "null")
    else:
        query = query.eq("parent_id", folder["parent_id"])

    if query.execute().data:
        raise HTTPException(status_code=409, detail="A folder with this name already exists")

    updated = (
        supabase.table("folders")
        .update({"name": name})
        .eq("id", folder_id)
        .eq("user_id", user_id)
        .execute()
    )

    return {
        "success": True,
        "name": name,
        "folder": updated.data[0] if updated.data else None
    }


@app.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("folders")
        .select("*")
        .eq("id", folder_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Folder not found")

    child_folders = (
        supabase.table("folders")
        .select("id")
        .eq("user_id", user_id)
        .eq("parent_id", folder_id)
        .execute()
    )
    if child_folders.data:
        raise HTTPException(
            status_code=409,
            detail="Folder is not empty. Delete or move its subfolders first."
        )

    child_files = (
        supabase.table("files")
        .select("id")
        .eq("user_id", user_id)
        .eq("folder_id", folder_id)
        .execute()
    )
    if child_files.data:
        raise HTTPException(
            status_code=409,
            detail="Folder is not empty. Move or delete its files first."
        )

    supabase.table("folders").delete().eq(
        "id", folder_id
    ).eq("user_id", user_id).execute()

    return {"success": True, "message": "Folder deleted successfully"}


# ============================================================
# FILE LIST
# ============================================================

@app.get("/files")
async def list_files(
    folder_id: int | None = None,
    user_id: int = Depends(get_current_user)
):
    verify_folder(folder_id, user_id)

    query = (
        supabase.table("files")
        .select("id,user_id,filename,size,storage_path,created_at,folder_id")
        .eq("user_id", user_id)
    )

    if folder_id is None:
        query = query.is_("folder_id", "null")
    else:
        query = query.eq("folder_id", folder_id)

    result = query.order("created_at", desc=True).execute()

    return {"success": True, "files": result.data}


# ============================================================
# GET FOLDER PATH / BREADCRUMBS
# ============================================================

@app.get("/folders/{folder_id}/path")
async def folder_path(
    folder_id: int,
    user_id: int = Depends(get_current_user)
):
    verify_folder(folder_id, user_id)

    path = []
    current_id = folder_id

    # Nested folders are expected to be small, so walking the tree is fine.
    for _ in range(100):
        result = (
            supabase.table("folders")
            .select("id,name,parent_id")
            .eq("id", current_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Folder not found")

        folder = result.data[0]
        path.append(folder)

        if folder["parent_id"] is None:
            break
        current_id = folder["parent_id"]

    path.reverse()
    return {"success": True, "path": path}


# ============================================================
# TEXT FILE
# ============================================================

@app.get("/files/{file_id}")
async def get_file(
    file_id: int,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]

    try:
        file_bytes = (
            supabase.storage.from_(BUCKET_NAME)
            .download(record["storage_path"])
        )
    except Exception as e:
        print("Storage read error:", e)
        raise HTTPException(status_code=500, detail="Could not read file")

    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="This file is not a text file")

    return {
        "success": True,
        "id": record["id"],
        "filename": record["filename"],
        "content": content
    }


# ============================================================
# RAW FILE
# ============================================================

@app.get("/files/{file_id}/raw")
async def get_raw_file(
    file_id: int,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]

    try:
        file_bytes = (
            supabase.storage.from_(BUCKET_NAME)
            .download(record["storage_path"])
        )
    except Exception as e:
        print("Raw storage download error:", e)
        raise HTTPException(status_code=500, detail="Could not read file")

    filename = record["filename"]
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    mime_types = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "bmp": "image/bmp", "ico": "image/x-icon",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "txt": "text/plain", "html": "text/html", "htm": "text/html",
        "css": "text/css", "js": "text/javascript",
        "json": "application/json", "xml": "application/xml",
        "zip": "application/zip"
    }

    media_type = mime_types.get(
        extension,
        mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-File-Name": filename
        }
    )


# ============================================================
# SAVE TEXT FILE
# ============================================================

@app.put("/files/{file_id}")
async def update_file(
    file_id: int,
    request: UpdateFileRequest,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]
    file_bytes = request.content.encode("utf-8")

    try:
        (
            supabase.storage.from_(BUCKET_NAME)
            .update(
                record["storage_path"],
                file_bytes,
                {"content-type": "text/plain", "upsert": "true"}
            )
        )
    except Exception as e:
        print("Storage update error:", e)
        raise HTTPException(status_code=500, detail="Could not update file")

    supabase.table("files").update({
        "size": len(file_bytes)
    }).eq("id", file_id).eq("user_id", user_id).execute()

    return {"success": True, "message": "File saved successfully"}


# ============================================================
# RENAME FILE
# ============================================================

@app.put("/files/{file_id}/rename")
async def rename_file(
    file_id: int,
    request: RenameFileRequest,
    user_id: int = Depends(get_current_user)
):
    new_filename = clean_name(request.filename)

    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]
    old_path = record["storage_path"]

    parts = old_path.rsplit("/", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=500, detail="Invalid storage path")

    directory, old_storage_filename = parts
    unique_prefix = old_storage_filename.split("_", 1)[0]
    new_storage_path = f"{directory}/{unique_prefix}_{new_filename}"

    try:
        supabase.storage.from_(BUCKET_NAME).move(
            old_path, new_storage_path
        )
    except Exception as e:
        print("Storage rename error:", e)
        raise HTTPException(status_code=500, detail="Could not rename file")

    try:
        supabase.table("files").update({
            "filename": new_filename,
            "storage_path": new_storage_path
        }).eq("id", file_id).eq("user_id", user_id).execute()
    except Exception as e:
        print("Database rename error:", e)
        raise HTTPException(status_code=500, detail="File renamed but database update failed")

    return {"success": True, "filename": new_filename}


# ============================================================
# DELETE FILE
# ============================================================

@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]

    try:
        supabase.storage.from_(BUCKET_NAME).remove([record["storage_path"]])
    except Exception as e:
        print("Storage delete error:", e)
        raise HTTPException(status_code=500, detail="Could not delete file from storage")

    supabase.table("files").delete().eq(
        "id", file_id
    ).eq("user_id", user_id).execute()

    return {"success": True, "message": "File deleted successfully"}


# ============================================================
# UPLOAD FILE
# ============================================================

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    folder_id: int | None = Form(None),
    user_id: int = Depends(get_current_user)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    verify_folder(folder_id, user_id)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty")

    unique_id = str(uuid.uuid4())
    storage_path = f"{user_id}/{unique_id}_{file.filename}"
    content_type = file.content_type or "application/octet-stream"

    try:
        (
            supabase.storage.from_(BUCKET_NAME)
            .upload(
                storage_path,
                file_bytes,
                {"content-type": content_type, "upsert": "false"}
            )
        )
    except Exception as e:
        print("Storage upload error:", e)
        raise HTTPException(status_code=500, detail="Could not upload file")

    try:
        result = (
            supabase.table("files")
            .insert({
                "user_id": user_id,
                "filename": file.filename,
                "storage_path": storage_path,
                "size": len(file_bytes),
                "folder_id": folder_id
            })
            .execute()
        )
    except Exception as e:
        print("Database insert error:", e)
        try:
            supabase.storage.from_(BUCKET_NAME).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Could not save file information")

    saved_file = result.data[0] if result.data else None

    return {
        "success": True,
        "message": "File uploaded successfully",
        "file": saved_file
    }


# ============================================================
# MOVE FILE
# ============================================================

@app.put("/files/{file_id}/move")
async def move_file(
    file_id: int,
    request: MoveFileRequest,
    user_id: int = Depends(get_current_user)
):
    # The file must belong to the logged-in user.
    result = (
        supabase.table("files")
        .select("id,folder_id")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    # None means the root/Home folder. Otherwise make sure the
    # destination folder belongs to this same user.
    verify_folder(request.folder_id, user_id)

    updated = (
        supabase.table("files")
        .update({"folder_id": request.folder_id})
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )

    return {
        "success": True,
        "message": "File moved successfully",
        "file": updated.data[0] if updated.data else None
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/download/{file_id}")
async def download_file(
    file_id: int,
    user_id: int = Depends(get_current_user)
):
    result = (
        supabase.table("files")
        .select("*")
        .eq("id", file_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="File not found")

    record = result.data[0]

    try:
        file_bytes = (
            supabase.storage.from_(BUCKET_NAME)
            .download(record["storage_path"])
        )
    except Exception as e:
        print("Storage download error:", e)
        raise HTTPException(status_code=500, detail="Could not download file")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{record["filename"]}"'
        }
    )
