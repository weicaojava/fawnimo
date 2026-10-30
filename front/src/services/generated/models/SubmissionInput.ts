/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReferenceInput } from './ReferenceInput';
/**
 * Client owns desired content and idempotency identity, never capability or cost.
 */
export type SubmissionInput = {
    confirm_project_charges?: boolean;
    expected_price_fingerprint?: (string | null);
    request_key: string;
    model_id: string;
    duration: number;
    references?: Array<ReferenceInput>;
    prompt: string;
    prompt_revision?: string;
};

