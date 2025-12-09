import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SOC_RE = re.compile(r"(sl16[248]0)")

def get_SoC() -> str | None:
    try:
        text = Path("/etc/hostname").read_text().strip()
        logger.info("Detected raw hostname: %s", text)
        m = _SOC_RE.search(text.lower())
        if not m:
            logger.warning("Unknown SoC in hostname: %s", text)
            return None
        soc = m.group(1)
        logger.info("Detected SoC: %s", soc)
        return soc
    except OSError:
        logger.warning("Failed to detect SoC")
        return None
    except Exception:
        logger.exception("Unexpected error while detecting SoC")
        return None


def has_npu() -> bool:
    soc = get_SoC()
    if not soc:
        logger.warning("Invalid SoC, defaulting to CPU execution")
        return False
    if soc == "sl1620":
        logger.info("Detected SoC SL1620, switching to CPU execution")
        return False
    return True


def validate_cpu_only(cpu_only: bool | None) -> bool:
    npu_available = has_npu()
    if cpu_only is None:
        cpu_only = not npu_available
    elif not npu_available and not cpu_only:
        logger.warning("NPU not available, switching to CPU execution")
        cpu_only = True
    return cpu_only


if __name__ == "__main__":
    pass
