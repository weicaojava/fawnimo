/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaDocument_Output } from './DnaDocument_Output';
import type { UnderstandingAttemptRead } from './UnderstandingAttemptRead';
import type { UnderstandingConnection } from './UnderstandingConnection';
import type { UnderstandingTranscript } from './UnderstandingTranscript';
/**
 * A persistent candidate remains unreviewed and inert until a human opens a new DNA draft.
 */
export type UnderstandingJobRead = {
    id: string;
    project_id: string;
    analysis_id: string;
    state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
    phase: string;
    attempt: number;
    progress: number;
    cancel_requested: boolean;
    reason: string;
    source: Record<string, any>;
    connection: UnderstandingConnection;
    document: (DnaDocument_Output | null);
    transcripts: Array<UnderstandingTranscript>;
    warnings: Array<string>;
    draft_id: (string | null);
    attempts: Array<UnderstandingAttemptRead>;
    created_at: number;
    updated_at: number;
};

