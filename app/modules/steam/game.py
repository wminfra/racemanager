from dataclasses import dataclass


@dataclass(frozen=True)
class GameLaunchMode:
    id: str
    label: str
    arguments: tuple[str, ...]
    vr_runtime: str | None = None


@dataclass(frozen=True)
class GameDefinition:
    name: str
    app_id: int
    executable_name: str
    launch_modes: tuple[GameLaunchMode, ...]

    def launch_mode(self, mode_id: str) -> GameLaunchMode:
        for mode in self.launch_modes:
            if mode.id == mode_id:
                return mode
        raise ValueError(f"Modo de inicialização não suportado por {self.name}: {mode_id}.")

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
    launch_modes=(
        GameLaunchMode("monitor", "Normal (monitor)", ("-nohmd",)),
        GameLaunchMode("oculus", "Oculus VR", ("-vr", "hmd=OculusHMD"), "oculus"),
        GameLaunchMode("steamvr", "SteamVR", ("-vr", "hmd=SteamVR"), "steamvr"),
    ),
)
