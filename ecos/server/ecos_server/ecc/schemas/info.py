#!/usr/bin/env python

from enum import Enum


class InfoEnum(Enum):
    home = "home"  # home information
    views = "views"  # information while switch web page
    layout = "layout"  # step design layout
    metrics = "metrics"  # step metrics
    subflow = "subflow"  # sub steps for this step
    analysis = "analysis"  # analysis metrics
    maps = "maps"  # maps for this step such as density map
    checklist = "checklist"  # step checklist
    sta = "sta"  # sta timing analysis
    config = "config"  # step configuration snapshot (e.g. flow_config path)


class NotifyEnum(Enum):
    step = "step"
    subflow = "subflow"
