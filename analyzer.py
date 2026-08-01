from config import CODE_KEYWORDS, PROJECT_KEYWORDS


class RequestAnalyzer:
    """Analyzes user requests to determine type and complexity."""

    @staticmethod
    def is_code_request(prompt: str) -> bool:
        """
        Determine if request is asking for code.
        
        Args:
            prompt: User's prompt
            
        Returns:
            True if it appears to be a code request
        """
        lower_prompt = prompt.lower()
        return any(keyword in lower_prompt for keyword in CODE_KEYWORDS)

    @staticmethod
    def is_project_request(prompt: str) -> bool:
        """
        Determine if request is for a multi-file project.
        
        Args:
            prompt: User's prompt
            
        Returns:
            True if it appears to be a project request
        """
        lower_prompt = prompt.lower()
        return any(keyword in lower_prompt for keyword in PROJECT_KEYWORDS)

    @staticmethod
    def should_use_project_planner(prompt: str) -> bool:
        """
        Determine if we should use project planning flow.
        
        A project planning flow is used when:
        - It's a code request AND
        - It's asking for a project/app/system
        
        Args:
            prompt: User's prompt
            
        Returns:
            True if project planner should be used
        """
        return RequestAnalyzer.is_code_request(prompt) and RequestAnalyzer.is_project_request(prompt)
