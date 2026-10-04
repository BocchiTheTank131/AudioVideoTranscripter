from abc import ABC, abstractmethod


class TranscriptionBackend(ABC):
    @abstractmethod
    def load_model(self, path, options): ...

    @abstractmethod
    def transcribe(self, audio, on_event): ...

    @abstractmethod
    def cancel(self): ...

    @abstractmethod
    def unload(self): ...

    @abstractmethod
    def get_capabilities(self): ...
