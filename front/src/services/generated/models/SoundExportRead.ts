/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SoundExportFile } from './SoundExportFile';
/**
 * A durable sound-v3 export, including the actual standalone audio mix.
 */
export type SoundExportRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    publication_id: string;
    publication_hash: string;
    task_id: string;
    state: 'queued' | 'running' | 'ready' | 'failed' | 'cancelled';
    reason: string;
    attempts: number;
    files: Array<SoundExportFile>;
    model_calls?: number;
};

