/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Return candidate identity and verified bytes metadata, never local paths or credentials.
 */
export type AssetImageJobRead = {
    id: string;
    asset_kind: 'actor' | 'costume' | 'scene' | 'prop';
    asset_id: string;
    slot_id: number;
    model_id: string;
    model_name: string;
    prompt: string;
    size: string;
    status: 'queued' | 'running' | 'candidate' | 'adopted' | 'failed' | 'cancelled';
    error: string;
    attempt: number;
    retry_of: (string | null);
    sha256: (string | null);
    byte_size: (number | null);
    width: (number | null);
    height: (number | null);
    mime: (string | null);
    file_id: (string | null);
    created_at: string;
    finished_at: (string | null);
    adopted_at: (string | null);
    content_url?: (string | null);
    provenance?: string;
    project_id?: (string | null);
    authorized_max_minor?: (string | null);
};

