/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Describe one explicit CAS adoption or historical selection intent.
 */
export type SelectionWrite = {
    request_key: string;
    shot_id: string;
    expected_revision: number;
    job_id?: (string | null);
    take_id?: (string | null);
    media_id?: (string | null);
    plan_id?: (string | null);
    accept_historical?: boolean;
    change_note?: string;
};

