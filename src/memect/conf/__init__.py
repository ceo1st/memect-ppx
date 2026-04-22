from pathlib import Path


def get_font_path(name:str)->Path:
    return Path(__file__).parent.joinpath(name)