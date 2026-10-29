/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UnderstandingConnection } from './UnderstandingConnection';
import type { UnderstandingModelChoice } from './UnderstandingModelChoice';
/**
 * Expose usable setup state without returning secret values or reading existing provider keys.
 */
export type UnderstandingConnectionRead = {
    project_id: string;
    revision: number;
    connection: UnderstandingConnection;
    configured: boolean;
    available: boolean;
    credential_present: boolean;
    worker_enabled: boolean;
    remote_allowed: boolean;
    message: string;
    next_steps: Array<string>;
    models?: Array<UnderstandingModelChoice>;
    planned_calls?: Array<'video_vision' | 'video_asr'>;
    price_fingerprints?: Record<string, string>;
    max_reservation_minor?: (number | null);
};

