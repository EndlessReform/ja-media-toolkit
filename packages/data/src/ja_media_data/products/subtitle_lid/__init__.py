"""Subtitle language-identification product."""

from ja_media_data.products.subtitle_lid.compiler import (
    SUBTITLE_LID_RECIPE_VERSION,
    compile_product,
)

__all__ = ["SUBTITLE_LID_RECIPE_VERSION", "compile_product"]
