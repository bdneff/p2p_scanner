from abc import ABC, abstractmethod

class Connector(ABC):
    @abstractmethod
    def fetch_markets(self):
        raise NotImplementedError
