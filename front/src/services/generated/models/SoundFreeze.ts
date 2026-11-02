/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Acknowledge picture, overlap and historical upstream changes before locking.
 */
export type SoundFreeze = {
    expected_revision: number;
    acknowledge_changes?: boolean;
    change_note?: string;
};

