from app.modules.steam.game import ASSETTO_CORSA_COMPETIZIONE, GameDefinition


class GameCatalog:
    """In-process capability registry; database rows remain the enabled-game list."""

    def __init__(self, definitions: tuple[GameDefinition, ...] = (ASSETTO_CORSA_COMPETIZIONE,)):
        self._definitions = {definition.name: definition for definition in definitions}

    def get(self, name: str) -> GameDefinition | None:
        return self._definitions.get(name)

    def modes_for(self, name: str) -> tuple:
        definition = self.get(name)
        return definition.launch_modes if definition else ()


GAME_CATALOG = GameCatalog()
