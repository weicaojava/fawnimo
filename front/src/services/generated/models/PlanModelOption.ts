/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Public trusted policy summary, explicitly not a real-model qualification.
 */
export type PlanModelOption = {
    model_id: string;
    durations: Array<number>;
    reference_kinds: Array<string>;
    max_references: number;
    quote_minor: (number | null);
    currency: string;
    iteration_limit: number;
    binding_hash: string;
};

