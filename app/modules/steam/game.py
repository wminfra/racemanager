from dataclasses import dataclass


@dataclass(frozen=True)
class GameDefinition:
    name: str
    app_id: int
    executable_name: str

    @property
    def launch_uri(self) -> str:
        return f"steam://rungameid/{self.app_id}"

    @property
    def manifest_name(self) -> str:
        return f"appmanifest_{self.app_id}.acf"


ASSETTO_CORSA_COMPETIZIONE = GameDefinition(
    name="Assetto Corsa Competizione",
    app_id=805550,
    executable_name="AC2-Win64-Shipping.exe",
)
