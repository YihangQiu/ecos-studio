#!/usr/bin/env python

from fastapi import APIRouter

from ..schemas import ECCRequest, ECCResponse
from ..services import ecc_service

ecc_serv = ecc_service()

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


@router.post("/create_workspace", response_model=ECCResponse)
def create_workspace(request: ECCRequest):
    """
    Create a new ECC project.
    """
    return ecc_serv.dispatch(request)


@router.post("/set_pdk_root", response_model=ECCResponse)
def set_pdk_root(request: ECCRequest):
    """
    Set PDK root path for backend runtime resolution.
    """
    return ecc_serv.dispatch(request)


@router.post("/load_workspace", response_model=ECCResponse)
def load_workspace(request: ECCRequest):
    """
    Open an existing ECC project.
    """
    return ecc_serv.dispatch(request)


@router.post("/delete_workspace", response_model=ECCResponse)
def delete_workspace(request: ECCRequest):
    """
    Delete an existing ECC project.
    """
    return ecc_serv.dispatch(request)


@router.post("/rtl2gds", response_model=ECCResponse)
def rtl2gds(request: ECCRequest):
    """
    run rtl2gds flow for current workspace.
    """
    return ecc_serv.dispatch(request)


@router.post("/run_step", response_model=ECCResponse)
def run_step(request: ECCRequest):
    """
    run step for current workspace.

    Sync route (not async) so dispatch runs in the default thread pool and does not
    block the asyncio event loop during long engine_flow.run_step() calls.
    """
    return ecc_serv.dispatch(request)


@router.post("/get_info", response_model=ECCResponse)
def get_info(request: ECCRequest):
    """
    get information by step and id.
    """
    return ecc_serv.dispatch(request)


@router.post("/get_flow_status", response_model=ECCResponse)
def get_flow_status(request: ECCRequest):
    """Return flow, foundation freshness, and async task status for a workspace."""
    return ecc_serv.dispatch(request)


@router.post("/get_artifact", response_model=ECCResponse)
def get_artifact(request: ECCRequest):
    """Read a redacted, workspace-confined text artifact."""
    return ecc_serv.dispatch(request)


@router.post("/extract_foundation_data", response_model=ECCResponse)
def extract_foundation_data(request: ECCRequest):
    """Extract read-only foundation summaries under foundation_data/ecc."""
    return ecc_serv.dispatch(request)


@router.post("/get_foundation_data", response_model=ECCResponse)
def get_foundation_data(request: ECCRequest):
    """Read extracted foundation summaries and stale status."""
    return ecc_serv.dispatch(request)


@router.post("/clone_workspace", response_model=ECCResponse)
def clone_workspace(request: ECCRequest):
    """Clone a workspace without switching the global loaded workspace."""
    return ecc_serv.dispatch(request)


@router.post("/run_from_step", response_model=ECCResponse)
def run_from_step(request: ECCRequest):
    """Queue background execution from a flow step."""
    return ecc_serv.dispatch(request)


@router.post("/update_parameters", response_model=ECCResponse)
def update_parameters(request: ECCRequest):
    """Apply guarded parameter updates to home/parameters.json."""
    return ecc_serv.dispatch(request)


@router.post("/update_step_config", response_model=ECCResponse)
def update_step_config(request: ECCRequest):
    """Record strategy config suggestions in an audit file only."""
    return ecc_serv.dispatch(request)


@router.post("/get_home_page", response_model=ECCResponse)
def get_home_page(request: ECCRequest):
    """
    get home page information.
    """
    return ecc_serv.dispatch(request)
