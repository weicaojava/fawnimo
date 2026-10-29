/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UnderstandingConnection } from './UnderstandingConnection';
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
};

