"""Optional Codex image input, guarded by the existing feature flag."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Union

from cores.llm.capabilities import vision_available

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Type alias for caller convenience
ImageInput = Union[str, bytes, "os.PathLike[str]"]
# A single image, or an ordered list of images (multi-image vision).
ImageInputs = Union[ImageInput, "list[ImageInput]"]


async def analyze_image(image_path_or_bytes, prompt, *, schema=None, model=None,
                        stage='vision'):
    """Optional image analysis through Codex; never an API-key fallback."""
    from prism_core.codex_subscription import run_stage
    import tempfile
    from pathlib import Path
    if not vision_available():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix='prism-vision-') as directory:
            images = image_path_or_bytes if isinstance(image_path_or_bytes,list) else [image_path_or_bytes]
            paths = []
            for index,image in enumerate(images):
                if isinstance(image,bytes):
                    path = Path(directory)/f'image-{index}.png'
                    path.write_bytes(image)
                else:
                    path = Path(image).resolve(strict=True)
                paths.append(path)
            text = await run_stage(stage,'Analyze the supplied images. Follow the requested output format.',
                                   prompt,images=paths,response_model=schema)
            return schema.model_validate_json(text) if schema else text
    except Exception as error:
        logger.warning('[VISION_ERROR] stage=%s type=%s',stage,type(error).__name__)
        return None
