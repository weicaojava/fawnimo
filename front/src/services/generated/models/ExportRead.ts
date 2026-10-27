/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportFileRead } from './ExportFileRead';
/**
 * Common task status plus immutable provenance and safe ready artifact descriptors.
 */
export type ExportRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    publication_id: string;
    publication_hash: string;
    task_id: string;
    state: 'queued' | 'running' | 'ready' | 'failed' | 'cancelled';
    reason: string;
    attempts: number;
    files: Array<ExportFileRead>;
    model_calls?: number;
};

