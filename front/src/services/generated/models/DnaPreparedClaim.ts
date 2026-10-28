/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A compiled statement keeps the human status, uncertainty and exact source locators.
 */
export type DnaPreparedClaim = {
    path: string;
    label: string;
    status: 'unknown' | 'observed' | 'inferred';
    zh: (string | null);
    en: (string | null);
    reason: string;
    confidence: (number | null);
    evidence: Array<Record<string, any>>;
};

