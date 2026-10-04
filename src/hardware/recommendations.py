def recommend(backend, available_ram):
    device = backend.device if backend else {}
    if not backend or backend.name == 'cpu':
        return {'fast': 'base', 'balanced': 'base', 'accurate': 'small' if available_ram >= 8 * 1024**3 else 'base'}
    free = device.get('memory_free') or device.get('memory_total') or 0
    if free >= 6 * 1024**3 and available_ram >= 8 * 1024**3:
        return {'fast': 'turbo', 'balanced': 'turbo', 'accurate': 'medium'}
    if free >= 3 * 1024**3:
        return {'fast': 'small', 'balanced': 'small', 'accurate': 'small'}
    return {'fast': 'base', 'balanced': 'base', 'accurate': 'small' if free >= 2 * 1024**3 else 'base'}


def preset_values(name, backend, available_ram):
    if name == 'custom':
        return {}
    return dict(model=recommend(backend, available_ram)[name], beam_size=1 if name == 'fast' else 5,
                temperature=0.0, vad=True, words=name == 'accurate')
