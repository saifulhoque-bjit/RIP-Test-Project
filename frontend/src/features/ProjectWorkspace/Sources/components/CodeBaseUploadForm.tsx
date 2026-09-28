import { useEffect, useRef, useState } from "react";
import type { DropdownOption } from "@/components/common/Dropdown";
import { useGetAppSettingsQuery } from "@/services/api/modules/settings";
import Dropdown from "@/components/common/Dropdown";
import TextArea from "@/components/common/TextArea";

interface CodeBaseUploadFormProps {
  onFormChange?: (formData: CodeBaseFormData) => void;
}

export interface CodeBaseFormData {
  scArchitectureType: string;
  sourceCodeLanguage: string;
  frontend: string;
  backend: string;
  infrastructure: string;
  architecture: string;
  database: string;
  codingStandards: string;
  databaseStrategy: string;
  architectureExpectation: string;
  security: string;
}

export default function CodeBaseUploadForm({
  onFormChange,
}: CodeBaseUploadFormProps) {
  const { data: settings } = useGetAppSettingsQuery();
  const lastEmittedFormRef = useRef<string>("");
  const [appliedLanguage, setAppliedLanguage] = useState<string>("");

  const [formData, setFormData] = useState<CodeBaseFormData>({
    scArchitectureType: "auto",
    sourceCodeLanguage: "",
    frontend: "",
    backend: "",
    infrastructure: "",
    architecture: "",
    database: "",
    codingStandards: "",
    databaseStrategy: "",
    architectureExpectation: "",
    security: "",
  });

  const toDropdownOptions = (
    items?: Array<{ key: string; value: string }>,
  ): DropdownOption[] => {
    return (items ?? []).map((item) => ({
      label: item.value,
      value: item.key,
    }));
  };

  const sourceCodeLanguageOptions = toDropdownOptions(
    settings?.source_code_pipeline.source_languages,
  );
  const frontendOptions = toDropdownOptions(
    settings?.source_code_pipeline.frontend_stacks,
  );
  const backendOptions = toDropdownOptions(
    settings?.source_code_pipeline.backend_stacks,
  );
  const infrastructureOptions = toDropdownOptions(
    settings?.source_code_pipeline.infrastructure_stacks,
  );
  const architectureOptions = toDropdownOptions(
    settings?.source_code_pipeline.architecture_stacks,
  );
  const databaseOptions = toDropdownOptions(
    settings?.source_code_pipeline.database_stacks,
  );

  const scArchitectureTypeOptions: DropdownOption[] = [
    { label: "Modular", value: "modular" },
    { label: "Non-Modular", value: "flat" },
    { label: "Unknown", value: "auto" },
  ];

  const buildEffectiveData = (data: CodeBaseFormData): CodeBaseFormData => ({
    ...data,
    scArchitectureType: data.scArchitectureType || "auto",
    sourceCodeLanguage:
      data.sourceCodeLanguage || sourceCodeLanguageOptions[0]?.value || "",
    frontend: data.frontend || frontendOptions[0]?.value || "",
    backend: data.backend || backendOptions[0]?.value || "",
    infrastructure:
      data.infrastructure || infrastructureOptions[0]?.value || "",
    architecture: data.architecture || architectureOptions[0]?.value || "",
    database: data.database || databaseOptions[0]?.value || "",
  });

  const effectiveFormData = buildEffectiveData(formData);

  const updateFormData = (field: keyof CodeBaseFormData, value: string) => {
    const updatedData = { ...formData, [field]: value };
    setFormData(updatedData);
    onFormChange?.(buildEffectiveData(updatedData));
  };

  const handleDropdownChange = (
    field: keyof CodeBaseFormData,
    option: DropdownOption,
  ) => {
    updateFormData(field, option.value);
  };

  const handleTextareaChange = (
    field: keyof CodeBaseFormData,
    e: React.ChangeEvent<HTMLTextAreaElement>,
  ) => {
    updateFormData(field, e.target.value);
  };

  const selectedSourceCodeLanguage =
    formData.sourceCodeLanguage || sourceCodeLanguageOptions[0]?.value || "";
  const selectedFrontend = formData.frontend || frontendOptions[0]?.value || "";
  const selectedBackend = formData.backend || backendOptions[0]?.value || "";
  const selectedInfrastructure =
    formData.infrastructure || infrastructureOptions[0]?.value || "";
  const selectedArchitecture =
    formData.architecture || architectureOptions[0]?.value || "";
  const selectedDatabase = formData.database || databaseOptions[0]?.value || "";

  if (
    selectedSourceCodeLanguage &&
    selectedSourceCodeLanguage !== appliedLanguage
  ) {
    const manifesto =
      settings?.source_code_pipeline.source_language_manifestos[
        selectedSourceCodeLanguage
      ];

    if (manifesto) {
      setAppliedLanguage(selectedSourceCodeLanguage);
      setFormData((previous) => ({
        ...previous,
        sourceCodeLanguage: selectedSourceCodeLanguage,
        codingStandards: manifesto.coding_standards,
        databaseStrategy: manifesto.database_strategy,
        architectureExpectation: manifesto.architecture,
        security: manifesto.security,
      }));
    }
  }

  useEffect(() => {
    if (!onFormChange) return;

    const hasAnyDefaultOptions =
      sourceCodeLanguageOptions.length > 0 ||
      frontendOptions.length > 0 ||
      backendOptions.length > 0 ||
      infrastructureOptions.length > 0 ||
      architectureOptions.length > 0 ||
      databaseOptions.length > 0;

    if (!hasAnyDefaultOptions) return;

    const serialized = JSON.stringify(effectiveFormData);
    if (serialized === lastEmittedFormRef.current) return;

    lastEmittedFormRef.current = serialized;
    onFormChange(effectiveFormData);
  }, [
    onFormChange,
    effectiveFormData,
    sourceCodeLanguageOptions,
    frontendOptions,
    backendOptions,
    infrastructureOptions,
    architectureOptions,
    databaseOptions,
  ]);

  return (
    <div className="w-full flex flex-col items-start gap-6">
      {/* Modular Folder Architecture */}
      <div className="w-full flex flex-col items-start gap-1.5">
        <h3 className="text-[length:var(--font-size-sm)] font-medium text-[color:var(--color-neutral-500)]">
          Source folders are organized by modules / file types?
        </h3>
        <Dropdown
          placeholder="Select architecture type"
          width="100%"
          options={scArchitectureTypeOptions}
          selected={formData.scArchitectureType || "auto"}
          onChange={(option) =>
            handleDropdownChange("scArchitectureType", option)
          }
        />
      </div>

      {/* Source code language type */}
      <div className="w-full flex flex-col items-start gap-1.5">
        <h3 className="text-[length:var(--font-size-sm)] font-medium text-[color:var(--color-neutral-500)]">
          Source code language type
        </h3>
        <Dropdown
          placeholder="Select language or platform"
          width="100%"
          options={sourceCodeLanguageOptions}
          selected={selectedSourceCodeLanguage}
          onChange={(option) =>
            handleDropdownChange("sourceCodeLanguage", option)
          }
        />
      </div>

      {/* Where to move */}
      <div className="w-full flex flex-col items-start gap-1.5">
        <h3 className="text-[length:var(--font-size-sm)] font-medium text-[color:var(--color-neutral-500)]">
          Where do you think we should move to?
        </h3>
        <div className="w-full flex flex-col items-start gap-1.5">
          <Dropdown
            placeholder="Frontend"
            width="100%"
            options={frontendOptions}
            selected={selectedFrontend}
            onChange={(option) => handleDropdownChange("frontend", option)}
          />
          <Dropdown
            placeholder="Backend"
            width="100%"
            options={backendOptions}
            selected={selectedBackend}
            onChange={(option) => handleDropdownChange("backend", option)}
          />
          <Dropdown
            placeholder="Infrastructure"
            width="100%"
            options={infrastructureOptions}
            selected={selectedInfrastructure}
            onChange={(option) =>
              handleDropdownChange("infrastructure", option)
            }
          />
          <Dropdown
            placeholder="Architecture"
            width="100%"
            options={architectureOptions}
            selected={selectedArchitecture}
            onChange={(option) => handleDropdownChange("architecture", option)}
          />
          <Dropdown
            placeholder="Database"
            width="100%"
            options={databaseOptions}
            selected={selectedDatabase}
            onChange={(option) => handleDropdownChange("database", option)}
          />
        </div>
      </div>

      {/* Your additional requirements */}
      <div className="w-full flex flex-col items-start gap-1.5">
        <h3 className="text-[length:var(--font-size-sm)] font-medium text-[color:var(--color-neutral-500)]">
          Your additional requirements
        </h3>
        <div className="w-full flex flex-col items-start gap-1.5">
          <TextArea
            label="Coding Standards"
            placeholder="Write your coding standards expectation"
            rows={2}
            value={formData.codingStandards}
            onChange={(e) => handleTextareaChange("codingStandards", e)}
          />
          <TextArea
            label="Database Strategy"
            placeholder="Write your data strategy expectation"
            rows={2}
            value={formData.databaseStrategy}
            onChange={(e) => handleTextareaChange("databaseStrategy", e)}
          />
          <TextArea
            label="Architecture"
            placeholder="Write your architecture expectation"
            rows={2}
            value={formData.architectureExpectation}
            onChange={(e) => handleTextareaChange("architectureExpectation", e)}
          />
          <TextArea
            label="Security"
            placeholder="Write your security expectation"
            rows={2}
            value={formData.security}
            onChange={(e) => handleTextareaChange("security", e)}
          />
        </div>
      </div>
    </div>
  );
}
