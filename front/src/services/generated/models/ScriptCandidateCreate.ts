/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Confirm one original-project text call against an exact saved source revision.
 */
export type ScriptCandidateCreate = {
    request_key: string;
    operation: 'optimize' | 'simplify' | 'divide' | 'extract_shot';
    source_field?: 'raw_text' | 'condensed_text';
    shot_id?: (string | null);
    expected_source_revision: number;
    expected_source_hash: string;
    model_id: string;
    expected_price_fingerprint: string;
    confirm_model_call: boolean;
};

