/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Confirm exact managed project charges and a recovery identity; immutable plan inputs remain server-owned.
 */
export type PlannedSubmission = {
    confirm_project_charges?: boolean;
    expected_price_fingerprint?: (string | null);
    request_key: string;
};

