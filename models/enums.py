from enum import Enum


class Layer(str, Enum):
    GP = "gp"
    INC = "inc"
    SNP = "snp"
    ODS = "ods"
    DDS = "dds"
    DM = "dm"
    DM_VIEW = "dm_view"


class CountMode(str, Enum):
    FAST = "fast"
    EXACT = "exact"


class StatusColor(str, Enum):
    RED = "red"
    YELLOW = "yellow"
    BLUE = "blue"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"
