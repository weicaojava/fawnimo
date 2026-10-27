/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Acknowledge duration, trim or historical upstream differences explicitly.
 */
export type EditFreezeWrite = {
    expected_revision: number;
    acknowledge_changes?: boolean;
    change_note?: string;
};

