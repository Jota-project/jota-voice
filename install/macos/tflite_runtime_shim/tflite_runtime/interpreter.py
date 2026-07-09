# Shim: re-exporta el Interpreter de ai-edge-litert bajo el nombre
# que espera wyoming-openwakeword. Si ai-edge-litert cambia la API,
# el único punto a tocar es esta línea.
from ai_edge_litert.interpreter import Interpreter

__all__ = ["Interpreter"]
