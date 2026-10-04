from utils.types import AppError


def validate_task(model, task):
    if task == 'translate' and (model.endswith('.en') or model == 'turbo'):
        raise AppError('Speech translation needs a multilingual non-Turbo model. Choose Base, Small, Medium or Large. English-only and Turbo models are for transcription.')
    if task not in ('transcribe', 'translate'):
        raise AppError('Unsupported task. Whisper translates speech into English only.')
