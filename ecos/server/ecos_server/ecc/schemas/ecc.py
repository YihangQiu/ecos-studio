#!/usr/bin/env python

from enum import Enum

from pydantic import BaseModel


class CMDEnum(Enum):
    home_page = "home_page"
    create_workspace = "create_workspace"
    set_pdk_root = "set_pdk_root"
    load_workspace = "load_workspace"
    delete_workspace = "delete_workspace"
    rtl2gds = "rtl2gds"
    run_step = "run_step"
    get_info = "get_info"
    get_flow_status = "get_flow_status"
    get_artifact = "get_artifact"
    extract_foundation_data = "extract_foundation_data"
    get_foundation_data = "get_foundation_data"
    clone_workspace = "clone_workspace"
    run_from_step = "run_from_step"
    update_parameters = "update_parameters"
    update_step_config = "update_step_config"
    notify = "notify"


class ResponseEnum(Enum):
    success = "success"
    failed = "failed"
    error = "error"
    warning = "warning"


class ECCRequest(BaseModel):
    """ """

    cmd: str
    data: dict


class ECCResponse(BaseModel):
    """ """

    cmd: str
    response: str
    data: dict
    message: list
