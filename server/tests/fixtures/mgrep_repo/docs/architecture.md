# Architecture Overview

## Components

The system consists of two main components:

### TokenParser

Splits raw text into tokens using a configurable delimiter.
Used by the pipeline to normalize whitespace before storing parsed content.

### FileCorpusService

Stores pre-populated file and documentation chunks for retrieval.

## Data Flow

1. A caller supplies prepared chunks to FileCorpusService.
2. FileCorpusService stores chunks in memory keyed by root + path + chunk_id.
3. QueryService performs keyword search across all stored chunks.

## Configuration

No configuration required for the in-memory backend.
