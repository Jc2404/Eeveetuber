# ADR-003: Own domain contracts

- Status: Accepted
- Date: 2026-08-19

## Decision

Events, ports, state machines, and persisted domain schemas belong to Eeveetuber. FastAPI,
SQLAlchemy, model SDK, MCP, renderer, and optional LangGraph types remain inside adapters.

## Consequence

Frameworks and providers can be replaced without migrating the character, media, memory, or plugin
public APIs.

