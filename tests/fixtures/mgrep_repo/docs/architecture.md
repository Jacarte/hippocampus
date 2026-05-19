# Architecture Overview

## Components

The system consists of three main components:

### TokenParser

Splits raw text into tokens using a configurable delimiter.
Used by the pipeline to normalise_whitespace before indexing.

### IndexingService

Responsible for scanning directories and pushing chunks into the FileCorpusService.

## Data Flow

1. Scanner walks the root directory and fingerprints files.
2. IndexingService chunks changed files via CodeChunker or MarkdownChunker.
3. FileCorpusService stores chunks in memory keyed by root + path + chunk_id.
4. QueryService performs keyword search across all stored chunks.

## Configuration

No configuration required for the in-memory backend.
