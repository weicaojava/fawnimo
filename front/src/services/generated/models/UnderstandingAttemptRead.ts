/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Keep exact input receipts and safe failure categories separate from model-quality claims.
 */
export type UnderstandingAttemptRead = {
    attempt: number;
    state: string;
    phase: string;
    started_at: (number | null);
    finished_at: (number | null);
    reason: string;
    input_receipt: (Record<string, any> | null);
    response_hash: (string | null);
};

