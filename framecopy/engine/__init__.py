from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from framecopy.engine.pipeline import ProcessingPipeline

__all__ = ["ProcessingPipeline"]


def __getattr__(name: str):
    if name == "ProcessingPipeline":
        from framecopy.engine.pipeline import ProcessingPipeline

        return ProcessingPipeline
    raise AttributeError(name)

