"""500 combinaciones editoriales, no 500 recomendaciones de apuestas."""
import json
from pathlib import Path

OPENERS = [
    "Pick en la mesa.", "La selección está lista.", "Una opción para revisar.",
    "Vamos con el análisis.", "La propuesta de hoy.", "Turno de revisar el mercado.",
    "Compartimos esta selección.", "Este es el pick.", "Una mirada al partido.",
    "La jugada propuesta.", "Aquí está la selección.", "Datos del pick a continuación.",
    "Un mercado para analizar.", "La previa tiene su pick.", "Ojo con esta selección.",
    "Ponemos el pick sobre la mesa.", "La lectura para este evento.",
    "Propuesta para la previa.", "Así queda la selección.", "Detalles de la jugada.",
    "Revisemos esta opción.", "El mercado elegido.", "Compartimos el pick del canal.",
    "Esta es la propuesta.", "Selección para este encuentro.",
]
CLOSERS = [
    "Revisa la cuota vigente.", "Las cuotas pueden cambiar.", "Sin resultados garantizados.",
    "Decide con calma.", "Respeta tus límites.", "Cada evento es distinto.",
    "Verifica el horario.", "No hace falta apostar siempre.", "El riesgo sigue presente.",
    "No persigas pérdidas.", "Comprueba las condiciones.", "Elige con información.",
    "El resultado no está escrito.", "La prudencia también cuenta.", "Revisa antes de decidir.",
    "Mantén la perspectiva.", "Evita decidir por impulso.", "Consulta el mercado actual.",
    "Tu presupuesto tiene límites.", "Puedes dejar pasar el pick.",
]

if __name__ == "__main__":
    phrases = [f"{a} {b}" for a in OPENERS for b in CLOSERS]
    assert len(set(phrases)) == 500
    Path(__file__).with_name("phrases.json").write_text(json.dumps(phrases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
