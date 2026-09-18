"""Реестр провайдеров, собранных по карточкам справочника.

Реестр меняется во время работы: правка карточки заменяет провайдера, удаление убирает его.
Прежний экземпляр при этом не закрывается сразу: ход закрепляет провайдера на всё своё время,
и идущий ход может ещё обращаться к нему. Такие экземпляры закрываются при остановке сервиса;
карточки правятся редко, поэтому их накопление несущественно.
"""

from ..errors import BadRequestError
from .base import ModelProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._retired: list[ModelProvider] = []

    def get(self, name: str) -> ModelProvider | None:
        return self._providers.get(name)

    def require(self, name: str) -> ModelProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise BadRequestError(
                f"Провайдера «{name}» нет в справочнике провайдеров. Добавьте его запросом "
                "POST /api/providers."
            )
        return provider

    def names(self) -> list[str]:
        return list(self._providers)

    def put(self, provider: ModelProvider) -> None:
        previous = self._providers.get(provider.name)
        if previous is not None:
            self._retired.append(previous)
        self._providers[provider.name] = provider

    def remove(self, name: str) -> None:
        previous = self._providers.pop(name, None)
        if previous is not None:
            self._retired.append(previous)

    async def aclose(self) -> None:
        for provider in [*self._providers.values(), *self._retired]:
            await provider.aclose()
        self._providers.clear()
        self._retired.clear()
